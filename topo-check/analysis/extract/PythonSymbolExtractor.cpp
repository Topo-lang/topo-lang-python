// PythonSymbolExtractor -- L1 regex-based Python symbol extraction.
//
// Extracts classes, functions, and methods from Python source files
// by parsing `class` and `def` statements with indentation-level scope
// tracking. This is the safety-net fallback when Pyright is unavailable.
//
// Python uses indentation for scope (no braces), so we track a scope
// stack: when a line's indent level is <= a scope entry's indent, that
// scope is popped.
//
// Multi-line-string detection invariant
// -------------------------------------
//
// The loop tracks `inMultilineString` to skip class/def detection inside
// docstrings and other triple-quoted spans. Detection is a heuristic
// (per-line counting of `"""` / `'''` substrings, odd count → enter
// multi-line). It is **deliberately not a Python tokenizer** — the
// authoritative L2 path uses the real Python `ast` module via the
// extractor subprocess (`topo_extract_python.py`). Real-world Python
// source rarely triggers these edges; adversarial / unusual code may.
//
// Known bounded limitations (issue
// topo-lang-python-symbolextractor-multiline-string-counter-heuristic):
//
//   1. Escaped quotes inside a string literal:
//        s = "\"\"\"text"
//      The loop sees one `"""` substring; counted as an entry. Bounded:
//      requires three consecutive `"` characters in source after escape
//      processing, which is unusual outside string-handling fixtures.
//
//   2. Mixed quote types in a single docstring:
//        """contains ''' triple"""
//      Both counters tick (dq=1, sq=1); multi-line entered. Bounded:
//      the closing `"""` on the same line cancels out only if the loop
//      revisits, so the heuristic may misattribute. Workaround: never
//      embed the other triple-quote variant in a docstring (rare).
//
//   3. Two triple-quoted strings on one expression with an odd-count
//      tail:
//        x = """a"""; y = """
//      Enters multi-line on the trailing `"""` until the next triple
//      quote. Bounded: this requires deliberate single-line concatenation
//      of triple-quoted literals, which Python style discourages.
//
//   4. Comment-embedded triple quotes:
//        # replace """ with ''' here
//      RESOLVED 2026-05-23: the loop now skips `#`-only lines BEFORE
//      counting triple quotes, so comment-embedded triple quotes no
//      longer enter multi-line mode.
//
// When detection misfires the loop hides subsequent `class` / `def`
// lines until the next triple-quote occurrence. Symptoms:
//   - CompletenessCheck reports `host symbol missing` for declared
//     symbols (caller blames the .topo, not the extractor).
//   - Containment L1 cannot attribute call sites to the right caller;
//     `<module>`-attributed violations look plausible but are wrong.
//
// Mitigation hierarchy (in increasing strength):
//   - L1 (this file): heuristic, accepts the bounded limitations above.
//   - L2 (ast subprocess): full Python parser, authoritative when an
//     LSP is configured.
//   - Future: replace this L1 path with a tokenize-based subprocess
//     too, eliminating the heuristic entirely. Not done because the
//     L2 path already covers the strict-correctness case and the
//     subprocess startup cost would hurt the fast-path UX.

#include "PythonSymbolExtractor.h"

#include <cctype>
#include <fstream>
#include <regex>
#include <string>
#include <vector>

namespace topo::check {

namespace {

struct ScopeEntry {
    std::string name;
    int indent;
    bool isClass;
};

/// Measure the indentation (number of leading spaces; tabs count as 4).
int measureIndent(const std::string& line) {
    int indent = 0;
    for (char c : line) {
        if (c == ' ')
            ++indent;
        else if (c == '\t')
            indent += 4;
        else
            break;
    }
    return indent;
}

/// Determine visibility from Python naming convention.
/// Dunder (__x__) -> Public, name-mangled (__x) -> Private,
/// convention-private (_x) -> Private, otherwise -> Public.
Visibility pythonVisibility(const std::string& name) {
    if (name.size() >= 4 && name.substr(0, 2) == "__" &&
        name.substr(name.size() - 2) == "__")
        return Visibility::Public;
    if (name.size() >= 2 && name.substr(0, 2) == "__")
        return Visibility::Private;
    if (!name.empty() && name[0] == '_')
        return Visibility::Private;
    return Visibility::Public;
}

/// Build a qualified name from the scope stack and a simple name.
/// If inside class Foo, method bar -> "Foo.bar".
/// If at module level, function baz -> "baz".
std::string buildQualifiedName(const std::vector<ScopeEntry>& scopeStack,
                               const std::string& simpleName) {
    // Walk the scope stack to find the nearest enclosing class
    for (auto it = scopeStack.rbegin(); it != scopeStack.rend(); ++it) {
        if (it->isClass) {
            return it->name + "." + simpleName;
        }
    }
    return simpleName;
}

/// Find the nearest enclosing class name from the scope stack.
std::string findEnclosingClass(const std::vector<ScopeEntry>& scopeStack) {
    for (auto it = scopeStack.rbegin(); it != scopeStack.rend(); ++it) {
        if (it->isClass) {
            return it->name;
        }
    }
    return "";
}

/// Check if line is blank or only whitespace.
bool isBlankLine(const std::string& line) {
    return line.find_first_not_of(" \t\r\n") == std::string::npos;
}

} // anonymous namespace

std::vector<HostSymbol> PythonSymbolExtractor::extractSymbols(const std::string& filePath) {
    std::vector<HostSymbol> result;
    std::ifstream file(filePath);
    if (!file.is_open()) return result;

    // Regexes for Python declarations
    static const std::regex classRegex(R"(^(\s*)class\s+(\w+))");
    static const std::regex funcRegex(R"(^(\s*)def\s+(\w+)\s*\()");
    static const std::regex decoratorRegex(R"(^\s*@(staticmethod|classmethod)\b)");

    std::vector<ScopeEntry> scopeStack;
    std::string line;
    int lineNum = 0;
    bool inMultilineString = false;
    bool nextIsStatic = false;

    while (std::getline(file, line)) {
        ++lineNum;

        // --- Multiline string tracking ---
        if (inMultilineString) {
            if (line.find("\"\"\"") != std::string::npos ||
                line.find("'''") != std::string::npos) {
                inMultilineString = false;
            }
            continue;
        }

        // Skip blank lines and comment-only lines BEFORE counting triple
        // quotes — a `# comment with """ literal here` would otherwise
        // tick the dq counter and falsely enter multi-line-string mode.
        // (Fix for issue
        // topo-lang-python-symbolextractor-multiline-string-counter-heuristic
        // case 5; the other heuristic edges — escaped quotes, mixed
        // quote types, multi-string single lines — are documented as
        // bounded limitations in the file-header invariant doc.)
        if (isBlankLine(line)) continue;
        size_t firstNonWs = line.find_first_not_of(" \t");
        if (firstNonWs != std::string::npos && line[firstNonWs] == '#') continue;

        // Detect start of multiline string (odd number of triple quotes on this line)
        {
            size_t dqCount = 0, sqCount = 0;
            for (size_t i = 0; i + 2 < line.size(); ++i) {
                if (line[i] == '"' && line[i + 1] == '"' && line[i + 2] == '"')
                    ++dqCount;
                else if (line[i] == '\'' && line[i + 1] == '\'' && line[i + 2] == '\'')
                    ++sqCount;
            }
            if ((dqCount % 2 == 1) || (sqCount % 2 == 1)) {
                inMultilineString = true;
                continue;
            }
        }

        int currentIndent = measureIndent(line);

        // Pop scope entries whose indent >= current indent
        // (current line has returned to or above a previous scope level)
        while (!scopeStack.empty() && scopeStack.back().indent >= currentIndent) {
            scopeStack.pop_back();
        }

        // Check for decorators
        std::smatch decoratorMatch;
        if (std::regex_search(line, decoratorMatch, decoratorRegex)) {
            std::string decorator = decoratorMatch[1].str();
            if (decorator == "staticmethod") {
                nextIsStatic = true;
            }
            continue;
        }

        // Check for class declaration
        std::smatch classMatch;
        if (std::regex_search(line, classMatch, classRegex)) {
            std::string className = classMatch[2].str();

            HostSymbol sym;
            sym.simpleName = className;
            sym.qualifiedName = className;
            sym.kind = HostSymbolKind::Class;
            sym.file = filePath;
            sym.line = lineNum;
            sym.hostVisibility = pythonVisibility(className);

            result.push_back(std::move(sym));

            // Push class scope
            scopeStack.push_back({className, currentIndent, /*isClass=*/true});

            // Reset decorator state
            nextIsStatic = false;
            continue;
        }

        // Check for function/method declaration
        std::smatch funcMatch;
        if (std::regex_search(line, funcMatch, funcRegex)) {
            std::string funcName = funcMatch[2].str();
            std::string enclosingClass = findEnclosingClass(scopeStack);
            bool insideClass = !enclosingClass.empty();

            HostSymbol sym;
            sym.simpleName = funcName;
            sym.file = filePath;
            sym.line = lineNum;
            sym.hostVisibility = pythonVisibility(funcName);

            if (insideClass) {
                sym.qualifiedName = buildQualifiedName(scopeStack, funcName);
                sym.enclosingClass = enclosingClass;

                if (funcName == "__init__") {
                    sym.kind = HostSymbolKind::Constructor;
                } else if (nextIsStatic) {
                    sym.kind = HostSymbolKind::StaticMethod;
                    sym.isStatic = true;
                } else {
                    sym.kind = HostSymbolKind::Method;
                }
            } else {
                sym.qualifiedName = funcName;
                sym.kind = HostSymbolKind::Function;
            }

            result.push_back(std::move(sym));

            // Push function scope (needed for nested functions/classes)
            scopeStack.push_back({funcName, currentIndent, /*isClass=*/false});

            // Reset decorator state
            nextIsStatic = false;
            continue;
        }

        // If we reach here without matching a decorator, class, or def,
        // reset decorator state (decorator was not followed by a def)
        nextIsStatic = false;
    }

    return result;
}

} // namespace topo::check
