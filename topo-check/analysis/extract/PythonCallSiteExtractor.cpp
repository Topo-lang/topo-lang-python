// PythonCallSiteExtractor -- L1 regex-based Python call site extraction.
//
// Scans Python source files for dangerous API calls using regex patterns.
// Uses indentation-level scope tracking (same approach as PythonSymbolExtractor)
// to determine the caller function for each detected call site.
//
// This is a safety-net fallback when Pyright LSP is unavailable.
// Design: false positives acceptable, false negatives are safety issues.

#include "PythonCallSiteExtractor.h"
#include "topo/Check/CapabilityCatalog.h"

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

/// Build a caller qualified name from the scope stack.
/// Class method: "ClassName.method_name"
/// Module function: "function_name"
/// Top level (no enclosing function): "<module>"
std::string buildCallerName(const std::vector<ScopeEntry>& scopeStack) {
    // Find the nearest enclosing function (the direct caller)
    std::string className;
    std::string funcName;

    for (auto it = scopeStack.rbegin(); it != scopeStack.rend(); ++it) {
        if (!it->isClass && funcName.empty()) {
            funcName = it->name;
        } else if (it->isClass && className.empty()) {
            className = it->name;
        }
    }

    if (funcName.empty()) return "<module>";
    if (!className.empty()) return className + "." + funcName;
    return funcName;
}

/// A pattern to scan for: a regex, the pattern name for reporting, and
/// the classification from PythonUnsafeCatalog.
struct DangerousPattern {
    std::regex regex;
    std::string patternName;
    UnsafeLevel level;
};

/// Build the set of dangerous patterns to scan for.
/// Called once (static initialization).
std::vector<DangerousPattern> buildPatterns() {
    std::vector<DangerousPattern> patterns;

    // Level 4: Language escape mechanisms
    patterns.push_back({
        std::regex(R"(\b(exec|eval|__import__|compile)\s*\()"),
        "", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\bos\.(system|popen|exec|execvp|execl|execle|execve)\s*\()"),
        "", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\bctypes\.(cdll|CDLL|windll)\b)"),
        "", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\bpickle\.(loads?|load|Unpickler)\s*\()"),
        "", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\bsys\._getframe\s*\()"),
        "sys._getframe", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\binspect\.(stack|currentframe|getinnerframes|getframeinfo)\s*\()"),
        "", UnsafeLevel::Escape
    });
    // --- issue #5: dynamic reflection (conservative posture) ---
    // Bare getattr/setattr/delattr without arg inspection — every call is unsafe.
    patterns.push_back({
        std::regex(R"(\b(getattr|setattr|delattr)\s*\()"),
        "", UnsafeLevel::Escape
    });
    // --- issue #6: serialization escape paths ---
    patterns.push_back({
        std::regex(R"(\bmarshal\.(loads?|load|dumps?|dump)\s*\()"),
        "", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\bshelve\.open\s*\()"),
        "shelve.open", UnsafeLevel::Escape
    });
    // yaml.load is unsafe unless an explicit SafeLoader is passed; we cannot
    // verify the argument in L1 so the bare call is conservatively flagged.
    patterns.push_back({
        std::regex(R"(\byaml\.load\s*\()"),
        "yaml.load", UnsafeLevel::Escape
    });
    // --- issue #6: metaprogramming / attribute punning ---
    // Match assignment to dunder attributes (e.g. `obj.__class__ = X`)
    // and `__dict__.update(...)` calls. Conservative: any access is flagged.
    patterns.push_back({
        std::regex(R"(\.__(class|bases|mro|dict|init_subclass)__\b)"),
        "", UnsafeLevel::Escape
    });
    // --- issue #6: frame / trace manipulation ---
    patterns.push_back({
        std::regex(R"(\bsys\.(settrace|setprofile)\s*\()"),
        "", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\bgc\.(get_referrers|get_objects)\s*\()"),
        "", UnsafeLevel::Escape
    });
    // --- issue #6: dynamic loading / monkey patching ---
    patterns.push_back({
        std::regex(R"(\bimportlib\.reload\s*\()"),
        "importlib.reload", UnsafeLevel::Escape
    });
    // --- issue #6: FFI / raw memory ---
    patterns.push_back({
        std::regex(R"(\bcffi\.FFI\s*\()"),
        "cffi.FFI", UnsafeLevel::Escape
    });
    patterns.push_back({
        std::regex(R"(\bmmap\.mmap\s*\()"),
        "mmap.mmap", UnsafeLevel::Escape
    });

    // Level 3: User input handling
    patterns.push_back({
        std::regex(R"(\binput\s*\()"),
        "input", UnsafeLevel::Input
    });
    patterns.push_back({
        std::regex(R"(\bflask\.request\b)"),
        "flask.request", UnsafeLevel::Input
    });

    // Level 1: System calls
    patterns.push_back({
        std::regex(R"(\bopen\s*\()"),
        "open", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\bos\.(open|read|write)\s*\()"),
        "", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\bsubprocess\.(run|Popen|call|check_output|check_call)\s*\()"),
        "", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\bsocket\.socket\s*\()"),
        "socket.socket", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\brequests\.(get|post|put|delete|patch|head)\s*\()"),
        "", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\bimportlib\.import_module\s*\()"),
        "importlib.import_module", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\bos\.fork\s*\()"),
        "os.fork", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\burlopen\s*\()"),
        "urlopen", UnsafeLevel::System
    });
    patterns.push_back({
        std::regex(R"(\bpathlib\.Path\b)"),
        "pathlib.Path", UnsafeLevel::System
    });

    return patterns;
}

/// Extract the matched pattern name from a regex match.
/// If the pattern has a fixed name, use that; otherwise extract from match.
std::string extractPatternName(const std::smatch& match, const std::string& fixedName) {
    if (!fixedName.empty()) return fixedName;
    // Use the full match text, trimmed of trailing whitespace/parens
    std::string text = match.str();
    // Remove trailing '('
    while (!text.empty() && (text.back() == '(' || text.back() == ' '))
        text.pop_back();
    return text;
}

} // anonymous namespace

std::vector<DetectedCallSite> PythonCallSiteExtractor::extractCallSites(const std::string& filePath) {
    std::vector<DetectedCallSite> results;
    std::ifstream file(filePath);
    if (!file.is_open()) return results;

    static const auto patterns = buildPatterns();
    static const std::regex classRegex(R"(^(\s*)class\s+(\w+))");
    static const std::regex funcRegex(R"(^(\s*)def\s+(\w+)\s*\()");

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

        // Detect start of multiline string (odd number of triple quotes)
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

        // Skip blank lines
        if (line.find_first_not_of(" \t\r\n") == std::string::npos) continue;

        // Skip comment-only lines
        size_t firstNonWs = line.find_first_not_of(" \t");
        if (firstNonWs != std::string::npos && line[firstNonWs] == '#') continue;

        // Strip inline comments for pattern matching
        // Find '#' that is not inside a string literal
        std::string codePart = line;
        {
            bool inSingleQuote = false, inDoubleQuote = false;
            for (size_t i = 0; i < codePart.size(); ++i) {
                char c = codePart[i];
                if (c == '\'' && !inDoubleQuote) inSingleQuote = !inSingleQuote;
                else if (c == '"' && !inSingleQuote) inDoubleQuote = !inDoubleQuote;
                else if (c == '#' && !inSingleQuote && !inDoubleQuote) {
                    codePart = codePart.substr(0, i);
                    break;
                }
            }
        }

        int currentIndent = measureIndent(line);

        // Pop scope entries whose indent >= current indent
        while (!scopeStack.empty() && scopeStack.back().indent >= currentIndent) {
            scopeStack.pop_back();
        }

        // Track scope: class and def statements
        std::smatch scopeMatch;
        if (std::regex_search(line, scopeMatch, classRegex)) {
            std::string className = scopeMatch[2].str();
            scopeStack.push_back({className, currentIndent, /*isClass=*/true});
            continue; // class line itself is not a call site
        }
        if (std::regex_search(line, scopeMatch, funcRegex)) {
            std::string funcName = scopeMatch[2].str();
            scopeStack.push_back({funcName, currentIndent, /*isClass=*/false});
            // Fall through: the def line might contain a dangerous default arg like open()
        }

        // Scan this line for dangerous patterns
        for (const auto& pat : patterns) {
            std::smatch match;
            std::string searchStr = codePart;
            // Search for all occurrences on this line
            while (std::regex_search(searchStr, match, pat.regex)) {
                std::string patternName = extractPatternName(match, pat.patternName);

                // Classify capability
                auto capability = classifyApiCall(patternName);

                DetectedCallSite site;
                site.calleePattern = patternName;
                site.callerQualifiedName = buildCallerName(scopeStack);
                site.capability = capability;
                site.unsafeLevel = pat.level;
                site.file = filePath;
                site.line = lineNum;
                results.push_back(std::move(site));

                // Advance past this match
                searchStr = match.suffix().str();
            }
        }
    }

    return results;
}

} // namespace topo::check
