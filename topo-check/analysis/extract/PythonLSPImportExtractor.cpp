// PythonLSPImportExtractor -- Clean line-based import extraction for Python.
//
// Python imports are deterministic syntax (no need for LSP).
// This extractor uses direct string matching with the same multiline-string
// state machine as PythonImportExtractor.

#include "PythonLSPImportExtractor.h"
#include "PythonUnsafeCatalog.h"
#include "analysis/extract/CppImportExtractor.h"

#include <cctype>
#include <cstring>
#include <fstream>
#include <string>

namespace topo::check {

namespace {

/// Skip leading whitespace, return position of first non-whitespace.
size_t skipWhitespace(const std::string& line, size_t pos) {
    while (pos < line.size() && (line[pos] == ' ' || line[pos] == '\t')) ++pos;
    return pos;
}

/// Read a Python identifier (alphanumeric + underscore + dots for module paths).
/// Returns the identifier and advances pos past it.
std::string readModuleName(const std::string& line, size_t& pos) {
    size_t start = pos;
    while (pos < line.size()) {
        char c = line[pos];
        if (std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '.') {
            ++pos;
        } else {
            break;
        }
    }
    return line.substr(start, pos - start);
}

/// Extract the top-level module from a possibly-dotted module path.
/// "os.path" -> "os", "socket" -> "socket"
std::string topLevelModule(const std::string& modulePath) {
    auto dot = modulePath.find('.');
    if (dot == std::string::npos) return modulePath;
    return modulePath.substr(0, dot);
}

/// Check if the line content starting at pos matches the given keyword,
/// followed by whitespace or end-of-line.
bool matchKeyword(const std::string& line, size_t pos, const char* keyword) {
    size_t len = std::strlen(keyword);
    if (pos + len > line.size()) return false;
    if (line.compare(pos, len, keyword) != 0) return false;
    // Keyword must be followed by whitespace, end-of-line, or non-alnum
    if (pos + len < line.size()) {
        char next = line[pos + len];
        if (std::isalnum(static_cast<unsigned char>(next)) || next == '_') return false;
    }
    return true;
}

} // anonymous namespace

std::vector<HostImport> PythonLSPImportExtractor::extractImports(const std::string& filePath) {
    std::vector<HostImport> results;
    std::ifstream file(filePath);
    if (!file.is_open()) return results;

    std::string line;
    int lineNum = 0;
    bool inMultilineString = false;

    while (std::getline(file, line)) {
        ++lineNum;

        // --- Join backslash-continued lines into a single logical line ---
        while (!line.empty() && line.back() == '\\') {
            line.pop_back(); // remove trailing backslash
            std::string next;
            if (std::getline(file, next)) {
                ++lineNum;
                auto pos = next.find_first_not_of(" \t");
                if (pos != std::string::npos) {
                    line += next.substr(pos);
                }
            } else {
                break;
            }
        }

        // --- Multiline string tracking ---
        if (inMultilineString) {
            auto dqPos = line.find("\"\"\"");
            auto sqPos = line.find("'''");
            size_t closePos = std::string::npos;
            constexpr size_t closeLen = 3;
            if (dqPos != std::string::npos && (sqPos == std::string::npos || dqPos < sqPos)) {
                closePos = dqPos;
            } else if (sqPos != std::string::npos) {
                closePos = sqPos;
            }
            if (closePos != std::string::npos) {
                inMultilineString = false;
                // Process code AFTER the closing triple-quote on this line
                line = line.substr(closePos + closeLen);
                if (line.find_first_not_of(" \t\r\n") == std::string::npos) continue;
                // Fall through to scan the remainder
            } else {
                continue;
            }
        }

        // Detect start of multiline string — erase same-line pairs,
        // enter multiline state on unmatched opener, keep code before it.
        {
            for (size_t i = 0; i + 2 < line.size(); ++i) {
                if ((line[i] == '"' && line[i+1] == '"' && line[i+2] == '"') ||
                    (line[i] == '\'' && line[i+1] == '\'' && line[i+2] == '\'')) {
                    char qc = line[i];
                    std::string closer(3, qc);
                    auto closePos = line.find(closer, i + 3);
                    if (closePos != std::string::npos) {
                        // Same-line triple-quote string: erase it and continue scanning
                        line.erase(i, closePos + 3 - i);
                        --i;
                        continue;
                    }
                    // Multiline string starts here — keep code BEFORE this position
                    line = line.substr(0, i);
                    inMultilineString = true;
                    break;
                }
            }
            if (inMultilineString && line.find_first_not_of(" \t\r\n") == std::string::npos) continue;
            // If inMultilineString was just set, line now contains only the part before """
            // Fall through to scan it
        }

        // Skip to first non-whitespace
        size_t pos = skipWhitespace(line, 0);
        if (pos >= line.size()) continue;

        // Skip comments
        if (line[pos] == '#') continue;

        // Match `from X import ...` (also handles wildcard `from X import *`,
        // issue #7 — the `*` token after `import` is irrelevant for module-level
        // classification: every public symbol of X enters the namespace).
        if (matchKeyword(line, pos, "from")) {
            pos += 4;
            pos = skipWhitespace(line, pos);
            std::string modulePath = readModuleName(line, pos);
            if (modulePath.empty()) continue;

            pos = skipWhitespace(line, pos);
            if (!matchKeyword(line, pos, "import")) continue;

            HostImport imp;
            imp.normalizedPath = topLevelModule(modulePath);
            imp.file = filePath;
            imp.line = lineNum;
            imp.unsafeLevel = PythonUnsafeCatalog::classifyImport(imp.normalizedPath);
            results.push_back(std::move(imp));
            continue;
        }

        // Match `import X` or `import X, Y, Z`
        if (matchKeyword(line, pos, "import")) {
            pos += 6;
            pos = skipWhitespace(line, pos);

            // Parse comma-separated module list
            while (pos < line.size()) {
                std::string modulePath = readModuleName(line, pos);
                if (modulePath.empty()) break;

                // Handle `import X as Y` -- skip the alias
                pos = skipWhitespace(line, pos);
                if (matchKeyword(line, pos, "as")) {
                    pos += 2;
                    pos = skipWhitespace(line, pos);
                    // Skip the alias name
                    readModuleName(line, pos);
                    pos = skipWhitespace(line, pos);
                }

                HostImport imp;
                imp.normalizedPath = topLevelModule(modulePath);
                imp.file = filePath;
                imp.line = lineNum;
                imp.unsafeLevel = PythonUnsafeCatalog::classifyImport(imp.normalizedPath);
                results.push_back(std::move(imp));

                // Check for comma
                if (pos < line.size() && line[pos] == ',') {
                    ++pos;
                    pos = skipWhitespace(line, pos);
                } else {
                    break;
                }
            }
            continue;
        }
    }

    return results;
}

std::vector<HostImport> PythonLSPImportExtractor::extractAll(const std::vector<std::string>& files) {
    std::vector<HostImport> results;
    for (const auto& f : files) {
        auto imports = extractImports(f);
        results.insert(results.end(),
                       std::make_move_iterator(imports.begin()),
                       std::make_move_iterator(imports.end()));
    }
    return results;
}

} // namespace topo::check
