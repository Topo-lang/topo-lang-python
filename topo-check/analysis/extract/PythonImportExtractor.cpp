// PythonImportExtractor -- L1 regex-based Python import extraction.
//
// Parses `import X` and `from X import Y` statements from Python source files.
// For dotted modules, uses the top-level component for classification
// (e.g. `from os.path import join` -> normalizedPath = "os").
//
// Handles multiline strings and comments to avoid false positives from
// import statements inside docstrings or commented-out code.

#include "PythonImportExtractor.h"
#include "PythonUnsafeCatalog.h"

#include <cctype>
#include <fstream>
#include <regex>
#include <string>
#include <vector>

namespace topo::check {

namespace {

/// Extract the top-level module from a possibly-dotted module path.
/// "os.path" -> "os", "socket" -> "socket"
std::string topLevelModule(const std::string& modulePath) {
    auto dot = modulePath.find('.');
    if (dot == std::string::npos) return modulePath;
    return modulePath.substr(0, dot);
}

} // anonymous namespace

std::vector<HostImport> PythonImportExtractor::extractImports(const std::string& filePath) {
    std::vector<HostImport> results;
    std::ifstream file(filePath);
    if (!file.is_open()) return results;

    // Patterns for import statements
    static const std::regex importRegex(R"(^\s*import\s+([\w.]+))");
    static const std::regex fromImportRegex(R"(^\s*from\s+([\w.]+)\s+import\b)");
    // issue #7: wildcard import `from X import *` — pulls every public symbol
    // of X into the current namespace, including its restricted APIs. We treat
    // it the same as `from X import Y` for classification purposes.
    static const std::regex wildcardImportRegex(R"(^\s*from\s+([\w.]+)\s+import\s+\*)");

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

        // Skip blank lines and comment-only lines BEFORE counting triple
        // quotes — a `# comment with """ literal here` would otherwise tick
        // the dq counter and falsely enter multi-line-string mode, swallowing
        // every later import. (Mirrors the ordering already fixed in
        // PythonSymbolExtractor; the other heuristic edges — escaped quotes,
        // mixed quote types — remain bounded limitations of the L1 path.)
        if (line.find_first_not_of(" \t\r\n") == std::string::npos) continue;
        size_t firstNonWs = line.find_first_not_of(" \t");
        if (firstNonWs != std::string::npos && line[firstNonWs] == '#') continue;

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

        // issue #7: explicit wildcard import handler — `from X import *`
        // pulls every public symbol of X into the current namespace,
        // including its restricted APIs. The classifier downstream treats
        // the module as if all its symbols were imported.
        std::smatch wildcardMatch;
        if (std::regex_search(line, wildcardMatch, wildcardImportRegex)) {
            std::string modulePath = wildcardMatch[1].str();
            std::string topModule = topLevelModule(modulePath);

            HostImport imp;
            imp.normalizedPath = topModule;
            imp.file = filePath;
            imp.line = lineNum;
            imp.unsafeLevel = PythonUnsafeCatalog::classifyImport(topModule);
            results.push_back(std::move(imp));
            continue;
        }

        // Match `from X import ...`
        std::smatch fromMatch;
        if (std::regex_search(line, fromMatch, fromImportRegex)) {
            std::string modulePath = fromMatch[1].str();
            std::string topModule = topLevelModule(modulePath);

            HostImport imp;
            imp.normalizedPath = topModule;
            imp.file = filePath;
            imp.line = lineNum;
            imp.unsafeLevel = PythonUnsafeCatalog::classifyImport(topModule);
            results.push_back(std::move(imp));
            continue;
        }

        // Match `import X` (possibly comma-separated: `import X, Y, Z`)
        std::smatch importMatch;
        if (std::regex_search(line, importMatch, importRegex)) {
            // Parse the rest of the import line for comma-separated modules.
            // Anchor at the start of the first captured module rather than a
            // hardcoded "import "(+7) offset off position(0): the match starts
            // at the line's leading whitespace, so a fixed offset lands mid-
            // keyword on indented `    import os` and yields garbage names.
            std::string rest = line.substr(
                static_cast<size_t>(importMatch.position(1)));

            // Trim leading whitespace (defensive; capture starts at the name)
            size_t start = rest.find_first_not_of(" \t");
            if (start == std::string::npos) continue;
            rest = rest.substr(start);

            // Parse comma-separated module list
            size_t pos = 0;
            while (pos < rest.size()) {
                // Read module name (alphanumeric + underscore + dots)
                size_t nameStart = pos;
                while (pos < rest.size() &&
                       (std::isalnum(static_cast<unsigned char>(rest[pos])) ||
                        rest[pos] == '_' || rest[pos] == '.')) {
                    ++pos;
                }
                if (pos == nameStart) break; // no module name found

                std::string modulePath = rest.substr(nameStart, pos - nameStart);
                std::string topModule = topLevelModule(modulePath);

                // Skip `as alias` if present
                size_t wsSkip = pos;
                while (wsSkip < rest.size() &&
                       (rest[wsSkip] == ' ' || rest[wsSkip] == '\t'))
                    ++wsSkip;
                if (wsSkip + 2 <= rest.size() && rest.substr(wsSkip, 2) == "as" &&
                    (wsSkip + 2 >= rest.size() ||
                     rest[wsSkip + 2] == ' ' || rest[wsSkip + 2] == '\t')) {
                    pos = wsSkip + 2;
                    // Skip whitespace after 'as'
                    while (pos < rest.size() &&
                           (rest[pos] == ' ' || rest[pos] == '\t'))
                        ++pos;
                    // Skip alias name
                    while (pos < rest.size() &&
                           (std::isalnum(static_cast<unsigned char>(rest[pos])) ||
                            rest[pos] == '_'))
                        ++pos;
                }

                HostImport imp;
                imp.normalizedPath = topModule;
                imp.file = filePath;
                imp.line = lineNum;
                imp.unsafeLevel = PythonUnsafeCatalog::classifyImport(topModule);
                results.push_back(std::move(imp));

                // Skip whitespace
                while (pos < rest.size() &&
                       (rest[pos] == ' ' || rest[pos] == '\t'))
                    ++pos;

                // Check for comma separator
                if (pos < rest.size() && rest[pos] == ',') {
                    ++pos;
                    // Skip whitespace after comma
                    while (pos < rest.size() &&
                           (rest[pos] == ' ' || rest[pos] == '\t'))
                        ++pos;
                } else {
                    break;
                }
            }
            continue;
        }
    }

    return results;
}

} // namespace topo::check
