// PythonSymbolAccessExtractor — L1 regex extractor for Python global writes.
//
// Strategy:
//   Pass 1: scan the file and collect module-level globals. A global is a
//           bare assignment whose left-hand side is at indent==0 and not
//           preceded by a `def` or `class` keyword on a parent indent.
//
//           Examples flagged as globals:
//             counter = 0
//             NAMES = ["a", "b"]
//             TABLE: dict = {}
//             flag, count = False, 0
//
//   Pass 2: re-scan and emit SymbolAccess{isWrite=true} for writes to these
//           globals inside function bodies. Writes include:
//             - simple assignment `name = ...`
//             - compound assignment `name += / -= / *= / /= / //= / %= / **=`
//             - subscript write `name[key] = ...`
//             - attribute write `name.attr = ...`
//             - explicit `global name; name = ...` (always treated as write
//               even if `name` was missed in Pass 1)
//
// Conservative posture: false positives are acceptable (user can ignore
// non-parallel functions); false negatives lose checker value. Method
// writes via `self.x = y` are NOT flagged — `self.x` is instance state.

#include "PythonSymbolAccessExtractor.h"

#include <cctype>
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

const std::unordered_set<std::string>& reservedNames() {
    static const std::unordered_set<std::string> kws = {
        "def", "class", "if", "elif", "else", "for", "while", "return",
        "yield", "raise", "try", "except", "finally", "with", "as", "pass",
        "break", "continue", "import", "from", "global", "nonlocal",
        "lambda", "del", "assert", "and", "or", "not", "in", "is",
        "True", "False", "None",
    };
    return kws;
}

bool isReserved(const std::string& name) {
    return reservedNames().count(name) > 0;
}

/// Pass 1: collect module-level globals.
std::unordered_set<std::string> collectGlobals(const std::string& filePath) {
    std::unordered_set<std::string> globals;
    std::ifstream file(filePath);
    if (!file.is_open()) return globals;

    static const std::regex classRegex(R"(^(\s*)class\s+(\w+))");
    static const std::regex funcRegex(R"(^(\s*)def\s+(\w+)\s*\()");

    // Module-level assignment: starts at indent 0, has the form
    //   name = expr
    //   name: type = expr
    //   name1, name2 = expr1, expr2
    static const std::regex assignRegex(R"(^([A-Za-z_][\w]*)\s*(?::[^=]+)?\s*=)");
    static const std::regex tupleAssignRegex(R"(^([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)+)\s*=)");

    std::vector<ScopeEntry> scopeStack;
    std::string line;
    bool inMultilineString = false;

    while (std::getline(file, line)) {
        // Multiline string tracking
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

        std::string codePart = stripInlineComment(line);
        std::string masked = maskStringLiterals(codePart);

        int currentIndent = measureIndent(line);

        // Pop scope entries whose indent >= current indent
        while (!scopeStack.empty() && scopeStack.back().indent >= currentIndent) {
            scopeStack.pop_back();
        }

        std::smatch classMatch;
        if (std::regex_search(line, classMatch, classRegex)) {
            scopeStack.push_back({classMatch[2].str(), currentIndent,
                                  /*isClass=*/true, /*isFunction=*/false});
            continue;
        }
        std::smatch funcMatch;
        if (std::regex_search(line, funcMatch, funcRegex)) {
            scopeStack.push_back({funcMatch[2].str(), currentIndent,
                                  /*isClass=*/false, /*isFunction=*/true});
            continue;
        }

        // Globals are at module scope (no class/function on the stack).
        if (!scopeStack.empty()) continue;
        // And the line itself must start at indent 0.
        if (currentIndent != 0) continue;

        // Try simple assignment first.
        std::smatch assignMatch;
        if (std::regex_search(masked, assignMatch, assignRegex)) {
            std::string name = assignMatch[1].str();
            // Reject `==` (comparison) — assignRegex pattern is greedy on
            // `=` so `name = ...` matches. But if the next char after the
            // assign token is `=`, treat as comparison.
            size_t eqPos = masked.find('=', assignMatch.position(0));
            if (eqPos != std::string::npos && eqPos + 1 < masked.size() &&
                masked[eqPos + 1] == '=') {
                // comparison, not assignment
            } else if (!isReserved(name)) {
                globals.insert(name);
            }
        }

        // Tuple unpacking: `a, b = ...`
        std::smatch tupMatch;
        if (std::regex_search(masked, tupMatch, tupleAssignRegex)) {
            std::string lhs = tupMatch[1].str();
            // Split on comma.
            size_t pos = 0;
            while (pos < lhs.size()) {
                size_t comma = lhs.find(',', pos);
                std::string name =
                    (comma == std::string::npos) ? lhs.substr(pos) : lhs.substr(pos, comma - pos);
                // trim
                while (!name.empty() && (name.front() == ' ' || name.front() == '\t'))
                    name.erase(name.begin());
                while (!name.empty() && (name.back() == ' ' || name.back() == '\t'))
                    name.pop_back();
                if (!name.empty() && !isReserved(name)) {
                    globals.insert(name);
                }
                if (comma == std::string::npos) break;
                pos = comma + 1;
            }
        }
    }

    return globals;
}

} // anonymous namespace

std::vector<SymbolAccess> PythonSymbolAccessExtractor::extractSymbolAccesses(
    const std::string& filePath) {
    std::vector<SymbolAccess> results;

    auto globals = collectGlobals(filePath);

    std::ifstream file(filePath);
    if (!file.is_open()) return results;

    static const std::regex classRegex(R"(^(\s*)class\s+(\w+))");
    static const std::regex funcRegex(R"(^(\s*)def\s+(\w+)\s*\()");
    static const std::regex globalDeclRegex(R"(^\s*global\s+(.+))");

    std::vector<ScopeEntry> scopeStack;
    // Per-function set of names declared `global` inside the function body.
    std::unordered_set<std::string> declaredGlobalsHere;

    std::string line;
    int lineNum = 0;
    bool inMultilineString = false;

    while (std::getline(file, line)) {
        ++lineNum;

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

        std::string codePart = stripInlineComment(line);
        std::string masked = maskStringLiterals(codePart);

        int currentIndent = measureIndent(line);

        // Pop scope entries whose indent >= current indent.
        while (!scopeStack.empty() && scopeStack.back().indent >= currentIndent) {
            // If we're popping a function scope, clear the per-function
            // global declaration set.
            if (scopeStack.back().isFunction) {
                declaredGlobalsHere.clear();
            }
            scopeStack.pop_back();
        }

        std::smatch classMatch;
        if (std::regex_search(line, classMatch, classRegex)) {
            scopeStack.push_back({classMatch[2].str(), currentIndent,
                                  /*isClass=*/true, /*isFunction=*/false});
            continue;
        }
        std::smatch funcMatch;
        if (std::regex_search(line, funcMatch, funcRegex)) {
            scopeStack.push_back({funcMatch[2].str(), currentIndent,
                                  /*isClass=*/false, /*isFunction=*/true});
            declaredGlobalsHere.clear();
            continue;
        }

        // Are we inside a function body?
        bool insideFunction = false;
        for (const auto& s : scopeStack) {
            if (s.isFunction) { insideFunction = true; break; }
        }
        if (!insideFunction) continue;

        // Detect `global X[, Y, Z]` declarations.
        std::smatch globalMatch;
        if (std::regex_search(masked, globalMatch, globalDeclRegex)) {
            std::string rest = globalMatch[1].str();
            size_t pos = 0;
            while (pos < rest.size()) {
                size_t comma = rest.find(',', pos);
                std::string name = (comma == std::string::npos) ? rest.substr(pos)
                                                                : rest.substr(pos, comma - pos);
                while (!name.empty() && (name.front() == ' ' || name.front() == '\t'))
                    name.erase(name.begin());
                while (!name.empty() &&
                       (name.back() == ' ' || name.back() == '\t' || name.back() == ';'))
                    name.pop_back();
                if (!name.empty() && !isReserved(name)) {
                    declaredGlobalsHere.insert(name);
                    // A bare `global X` declaration also adds X to the
                    // overall globals set so subsequent assignments are
                    // detected even if Pass 1 missed it.
                    globals.insert(name);
                }
                if (comma == std::string::npos) break;
                pos = comma + 1;
            }
            continue;
        }

        std::string callerName = buildCallerName(scopeStack);

        // For each candidate global, look for write patterns on this line.
        // Iterate the union of (Pass-1 globals + per-function declared
        // globals) — both contribute equivalent writes.
        for (const auto& name : globals) {
            // Find every position of `name` on the line.
            size_t pos = 0;
            while (pos < masked.size()) {
                size_t found = masked.find(name, pos);
                if (found == std::string::npos) break;

                // Word boundary BEFORE
                bool leftOK = true;
                if (found > 0) {
                    char prev = masked[found - 1];
                    if (std::isalnum(static_cast<unsigned char>(prev)) || prev == '_')
                        leftOK = false;
                    // Member-access via `self.name` or `obj.name` is NOT
                    // a write to a global — it writes instance state.
                    if (prev == '.') leftOK = false;
                }
                // Word boundary AFTER (the name itself)
                size_t end = found + name.size();
                bool rightOK = true;
                if (end < masked.size()) {
                    char nxt = masked[end];
                    if (std::isalnum(static_cast<unsigned char>(nxt)) || nxt == '_')
                        rightOK = false;
                }
                if (!leftOK || !rightOK) {
                    pos = found + 1;
                    continue;
                }

                // Check the right-hand context for a write operator.
                // Possible patterns:
                //   name = ...           (but not name == / != / <= / >=)
                //   name += / -= / ...
                //   name[ ... ] = ...
                //   name.attr = ...
                size_t after = end;
                while (after < masked.size() && (masked[after] == ' ' || masked[after] == '\t'))
                    ++after;

                bool isWrite = false;
                if (after < masked.size()) {
                    char c = masked[after];
                    if (c == '=' && (after + 1 >= masked.size() || masked[after + 1] != '=')) {
                        isWrite = true;
                    }
                    if (!isWrite && after + 1 < masked.size() && masked[after + 1] == '=') {
                        if (c == '+' || c == '-' || c == '*' || c == '/' || c == '%' ||
                            c == '&' || c == '|' || c == '^') {
                            isWrite = true;
                        }
                    }
                    if (!isWrite && after + 2 < masked.size() && masked[after + 2] == '=') {
                        if ((c == '<' && masked[after + 1] == '<') ||
                            (c == '>' && masked[after + 1] == '>') ||
                            (c == '/' && masked[after + 1] == '/') ||
                            (c == '*' && masked[after + 1] == '*')) {
                            isWrite = true;
                        }
                    }
                    // Subscript write: name[ ... ] = expr
                    if (!isWrite && c == '[') {
                        // Find the matching `]` then check next non-ws is `=`.
                        size_t bracketDepth = 1;
                        size_t j = after + 1;
                        while (j < masked.size() && bracketDepth > 0) {
                            if (masked[j] == '[') ++bracketDepth;
                            else if (masked[j] == ']') --bracketDepth;
                            ++j;
                        }
                        if (bracketDepth == 0) {
                            while (j < masked.size() && (masked[j] == ' ' || masked[j] == '\t'))
                                ++j;
                            if (j < masked.size() && masked[j] == '=' &&
                                (j + 1 >= masked.size() || masked[j + 1] != '=')) {
                                isWrite = true;
                            }
                        }
                    }
                    // Attribute write: name.attr = expr (any depth of attribute chain)
                    if (!isWrite && c == '.') {
                        // Find the next `=` not preceded by `==/!=/<=/>=`.
                        size_t j = after;
                        while (j < masked.size() && masked[j] != '=' && masked[j] != ';') {
                            ++j;
                        }
                        if (j < masked.size() && masked[j] == '=' &&
                            (j + 1 >= masked.size() || masked[j + 1] != '=')) {
                            // Make sure it's not `<=` or `>=`.
                            if (j == 0 || (masked[j - 1] != '<' && masked[j - 1] != '>' &&
                                            masked[j - 1] != '!' && masked[j - 1] != '=')) {
                                isWrite = true;
                            }
                        }
                    }
                }

                if (isWrite) {
                    SymbolAccess access;
                    access.function = callerName;
                    access.symbol = name;
                    access.isWrite = true;
                    access.file = filePath;
                    access.line = lineNum;
                    results.push_back(std::move(access));
                    // One write per global per line is enough to flag.
                    break;
                }
                pos = found + name.size();
            }
        }
    }

    return results;
}

} // namespace topo::check
