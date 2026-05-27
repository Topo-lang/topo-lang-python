// PythonCallEdgeExtractor — L1 regex extractor for Python caller→callee edges.
//
// Mirrors PythonCallSiteExtractor's indent-based scope tracking. Python uses
// indentation (no braces) so the scope state machine pops entries whose
// indent >= the current line's indent.
//
// For each identifier call inside a function body, emits a CallEdge with:
//   - caller qualified by the nearest enclosing class (e.g. "Cls.method"),
//     or just the function name at module scope ("foo")
//   - callee = the call target token. Bare `bar(...)` → "bar". Attribute
//     calls like `obj.bar(...)` are emitted twice — once with the simple
//     name "bar" so the algorithm matches against `calledFunctions` entries
//     that are simple names, and once with the dotted form "obj.bar" so
//     visibility checks against module-qualified names still work.
//
// Filter rules:
//   - Skip Python keywords (`if`, `for`, `while`, `print`, `len`, ...)
//   - Skip the function definition `def foo(...):` itself
//   - Skip dunder calls inside f-strings or arguments (best effort)

#include "PythonCallEdgeExtractor.h"

#include <cctype>
#include <filesystem>
#include <fstream>
#include <regex>
#include <string>
#include <unordered_set>
#include <vector>

namespace topo::check {

namespace {

struct ScopeEntry {
    std::string name;
    int indent;
    bool isClass;
    bool isFunction;
};

/// Derive a "module namespace" hint from the file path's stem.
/// `src/app.py` → `app`, `lib/foo/bar.py` → `bar`. The hint is used to
/// emit synthesized qualified call edges so Python source (which has no
/// source-level `namespace` blocks) can still match `.topo` declarations
/// like `namespace app { private: void helper(); }`.
std::string fileNamespaceHint(const std::string& filePath) {
    namespace fs = std::filesystem;
    fs::path p(filePath);
    return p.stem().string();
}

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

bool isBlankLine(const std::string& line) {
    return line.find_first_not_of(" \t\r\n") == std::string::npos;
}

/// Build the caller qualified name from the scope stack.
/// Walks down to find the nearest enclosing function and the nearest
/// enclosing class.
///   - inside class C, def m() → "C.m"
///   - module-level def f()    → "f"
///   - nested def f() inside class C, def m() → "C.m" (we attribute to
///     the outermost class+function for L1 simplicity)
std::string buildCallerName(const std::vector<ScopeEntry>& scopeStack) {
    std::string className;
    std::string funcName;
    for (auto it = scopeStack.rbegin(); it != scopeStack.rend(); ++it) {
        if (it->isFunction && funcName.empty()) {
            funcName = it->name;
        } else if (it->isClass && className.empty()) {
            className = it->name;
        }
    }
    if (funcName.empty()) return "<module>";
    if (!className.empty()) return className + "." + funcName;
    return funcName;
}

/// Python builtins / keywords that look like calls but are not user-defined
/// function references — must NOT be emitted as call edges.
const std::unordered_set<std::string>& pythonBuiltinsAndKeywords() {
    static const std::unordered_set<std::string> kws = {
        // Control flow / statements
        "if", "elif", "else", "for", "while", "return", "yield", "raise",
        "try", "except", "finally", "with", "as", "pass", "break",
        "continue", "import", "from", "global", "nonlocal", "lambda",
        "del", "assert", "and", "or", "not", "in", "is",
        // Class / def keywords
        "def", "class",
        // Type literals (true callable but rarely meaningful as edges)
        "True", "False", "None",
        // Common builtins that aren't user-defined symbols
        "print", "len", "range", "str", "int", "float", "bool", "list",
        "dict", "set", "tuple", "frozenset", "bytes", "bytearray",
        "type", "isinstance", "issubclass", "hash", "id", "abs", "min",
        "max", "sum", "any", "all", "map", "filter", "zip", "enumerate",
        "reversed", "sorted", "iter", "next", "open", "input", "format",
        "repr", "ord", "chr", "hex", "oct", "bin", "round", "pow",
        "divmod", "complex", "object", "super", "vars", "dir", "callable",
        "globals", "locals", "help", "exit", "quit",
        // Exception types
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "RuntimeError", "AttributeError", "NotImplementedError",
        "StopIteration", "ArithmeticError", "OverflowError",
        "ZeroDivisionError", "FileNotFoundError", "OSError",
    };
    return kws;
}

bool isBuiltinOrKeyword(const std::string& name) {
    return pythonBuiltinsAndKeywords().count(name) > 0;
}

/// Strip a trailing `:` and inline `#` comment from a Python code line.
std::string stripInlineComment(const std::string& line) {
    bool inSingleQuote = false, inDoubleQuote = false;
    for (size_t i = 0; i < line.size(); ++i) {
        char c = line[i];
        if (c == '\\' && i + 1 < line.size()) { ++i; continue; }
        if (c == '\'' && !inDoubleQuote) inSingleQuote = !inSingleQuote;
        else if (c == '"' && !inSingleQuote) inDoubleQuote = !inDoubleQuote;
        else if (c == '#' && !inSingleQuote && !inDoubleQuote) {
            return line.substr(0, i);
        }
    }
    return line;
}

/// Mask string literal contents (single + double + triple quotes already
/// handled by upstream multiline-string state) so that call-like tokens
/// inside literals don't get emitted.
std::string maskStringLiterals(const std::string& line) {
    std::string out = line;
    for (size_t i = 0; i < out.size(); ++i) {
        char c = out[i];
        if (c == '"') {
            out[i] = ' ';
            ++i;
            while (i < out.size() && out[i] != '"') {
                if (out[i] == '\\' && i + 1 < out.size()) {
                    out[i] = ' ';
                    out[i + 1] = ' ';
                    i += 2;
                    continue;
                }
                out[i] = ' ';
                ++i;
            }
            if (i < out.size()) out[i] = ' ';
        } else if (c == '\'') {
            out[i] = ' ';
            ++i;
            while (i < out.size() && out[i] != '\'') {
                if (out[i] == '\\' && i + 1 < out.size()) {
                    out[i] = ' ';
                    out[i + 1] = ' ';
                    i += 2;
                    continue;
                }
                out[i] = ' ';
                ++i;
            }
            if (i < out.size()) out[i] = ' ';
        }
    }
    return out;
}

} // anonymous namespace

std::vector<CallEdge> PythonCallEdgeExtractor::extractCallEdges(const std::string& filePath) {
    std::vector<CallEdge> results;
    std::ifstream file(filePath);
    if (!file.is_open()) return results;

    static const std::regex classRegex(R"(^(\s*)class\s+(\w+))");
    static const std::regex funcRegex(R"(^(\s*)def\s+(\w+)\s*\()");
    // Match call targets: optional dotted prefix + identifier + `(`.
    // Group 1 captures the full callee (with dots).
    static const std::regex callRegex(R"(([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s*\()");

    // Hint used to synthesize file-qualified caller/callee forms — e.g.
    // for `app.py` we additionally emit edges with caller/callee prefixed
    // by `app::`. This lets the .topo `namespace app { ... }` declarations
    // match the call edges without the Python source needing namespace
    // syntax.
    const std::string nsHint = fileNamespaceHint(filePath);

    std::vector<ScopeEntry> scopeStack;
    std::string line;
    int lineNum = 0;
    bool inMultilineString = false;

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

        if (isBlankLine(line)) continue;
        size_t firstNonWs = line.find_first_not_of(" \t");
        if (firstNonWs != std::string::npos && line[firstNonWs] == '#') continue;

        // Strip inline comments and mask string literals.
        std::string codePart = stripInlineComment(line);
        std::string scanLine = maskStringLiterals(codePart);

        int currentIndent = measureIndent(line);

        // Pop scope entries whose indent >= current indent
        while (!scopeStack.empty() && scopeStack.back().indent >= currentIndent) {
            scopeStack.pop_back();
        }

        // Detect class declaration
        std::smatch classMatch;
        if (std::regex_search(line, classMatch, classRegex)) {
            std::string className = classMatch[2].str();
            scopeStack.push_back({className, currentIndent, /*isClass=*/true, /*isFunction=*/false});
            continue;
        }

        // Detect function/method declaration
        std::smatch funcMatch;
        if (std::regex_search(line, funcMatch, funcRegex)) {
            std::string funcName = funcMatch[2].str();
            scopeStack.push_back({funcName, currentIndent, /*isClass=*/false, /*isFunction=*/true});
            // The def line itself is a signature; the body starts on subsequent lines.
            continue;
        }

        // Only scan for calls when we are inside a function scope.
        bool insideFunction = false;
        for (const auto& s : scopeStack) {
            if (s.isFunction) { insideFunction = true; break; }
        }
        if (!insideFunction) continue;

        std::string callerName = buildCallerName(scopeStack);

        // Iterate all call-like tokens on this line.
        std::string remaining = scanLine;
        size_t absOffset = 0;
        while (true) {
            std::smatch m;
            if (!std::regex_search(remaining, m, callRegex)) break;
            std::string callee = m[1].str();
            size_t matchPos = absOffset + static_cast<size_t>(m.position(1));
            size_t matchLen = m[1].length();

            // Extract the "simple" (last) name for keyword filtering.
            std::string simple = callee;
            auto dotPos = callee.rfind('.');
            if (dotPos != std::string::npos) {
                simple = callee.substr(dotPos + 1);
            }

            bool skip = false;
            if (simple.empty() ||
                (!std::isalpha(static_cast<unsigned char>(simple[0])) && simple[0] != '_')) {
                skip = true;
            }
            if (!skip && isBuiltinOrKeyword(simple)) skip = true;
            // Skip the leftmost name in dotted path if it's a keyword too
            // (e.g. `True.foo(` should not be emitted).
            if (!skip && dotPos != std::string::npos) {
                std::string head = callee.substr(0, callee.find('.'));
                if (isBuiltinOrKeyword(head)) skip = true;
            }

            // Skip dunder methods that look like internal Python machinery.
            // Allow user-defined methods that start with single underscore
            // ("private" by convention) — visibility check needs them.
            if (!skip && simple.size() >= 4 &&
                simple.substr(0, 2) == "__" &&
                simple.substr(simple.size() - 2) == "__") {
                skip = true;
            }

            // Skip if callee is identical to the current function and the
            // line started with `def ` — that's the def line, not a call.
            // We already `continue`d above on def detection so this is
            // belt-and-suspenders defensive.
            if (!skip) {
                size_t firstNonWs = scanLine.find_first_not_of(" \t");
                if (firstNonWs != std::string::npos &&
                    scanLine.compare(firstNonWs, 4, "def ") == 0) {
                    skip = true;
                }
            }

            // Verify we're at a token boundary — the previous char must not
            // be alphanumeric or `_` (otherwise we matched a substring of an
            // identifier).
            if (!skip && matchPos > 0) {
                char prev = scanLine[matchPos - 1];
                if (std::isalnum(static_cast<unsigned char>(prev)) || prev == '_') {
                    skip = true;
                }
            }

            if (!skip) {
                // Convert Python dotted form (`app.helper`) to Topo `::`
                // qualified form (`app::helper`) so call edges can be
                // matched against VisibilityEntry::qualifiedName which uses
                // `::` as the namespace separator.
                std::string canonicalCallee = callee;
                {
                    std::string out;
                    for (size_t k = 0; k < canonicalCallee.size(); ++k) {
                        if (canonicalCallee[k] == '.') out += "::";
                        else out += canonicalCallee[k];
                    }
                    canonicalCallee = std::move(out);
                }

                // Emit the natural (canonicalized) form.
                CallEdge edge;
                edge.caller = callerName;
                edge.callee = canonicalCallee;
                edge.file = filePath;
                edge.line = lineNum;
                results.push_back(edge);

                // If the callee is dotted, also emit an edge with the simple
                // name so check algorithms that store simple names (as the
                // .topo `stage<1> compute()` form does) still match.
                if (dotPos != std::string::npos && !simple.empty()) {
                    CallEdge simpleEdge;
                    simpleEdge.caller = callerName;
                    simpleEdge.callee = simple;
                    simpleEdge.file = filePath;
                    simpleEdge.line = lineNum;
                    results.push_back(simpleEdge);
                }

                // Synthesize a `<ns>::caller` / `<ns>::callee` qualified form
                // using the file's stem as a namespace hint. This lets the
                // VisibilityCheck (which keys by `nsPath::funcName`) and
                // StageIsolationCheck match Python source against `.topo`
                // declarations like `namespace app { ... }` when the file
                // is named `app.py`. The natural-form edge above is still
                // emitted so simple-name lookups also work.
                if (!nsHint.empty()) {
                    CallEdge qualifiedEdge;
                    qualifiedEdge.caller = nsHint + "::" + callerName;
                    qualifiedEdge.callee = nsHint + "::" + simple;
                    qualifiedEdge.file = filePath;
                    qualifiedEdge.line = lineNum;
                    results.push_back(std::move(qualifiedEdge));
                }
            }

            size_t advance = static_cast<size_t>(m.position(1)) + matchLen;
            if (advance == 0) advance = 1;
            remaining = remaining.substr(advance);
            absOffset += advance;
        }
    }

    return results;
}

} // namespace topo::check
