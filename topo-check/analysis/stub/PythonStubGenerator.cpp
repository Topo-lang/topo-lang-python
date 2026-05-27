// PythonStubGenerator — Stub function bodies in Python source files.
//
// Strategy:
// 1. Read the source file
// 2. Split into lines, search for `def funcName(` or `async def funcName(`
// 3. Track parenthesis balance for multi-line signatures until the closing `:`
// 4. Determine the body range via indentation-level detection
// 5. Extract return type annotation from the signature
// 6. Replace the body with a trivial stub based on return type
// 7. Write the modified source back
// 8. Preserve original content for restoration

#include "PythonStubGenerator.h"

#include <fstream>
#include <regex>
#include <sstream>
#include <vector>

namespace topo::check {

namespace {

/// Read entire file into string.
bool readFile(const std::string& path, std::string& content) {
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) return false;
    std::ostringstream ss;
    ss << ifs.rdbuf();
    content = ss.str();
    return true;
}

/// Write string to file, replacing contents.
bool writeFile(const std::string& path, const std::string& content) {
    std::ofstream ofs(path, std::ios::binary | std::ios::trunc);
    if (!ofs) return false;
    ofs << content;
    return ofs.good();
}

/// Split text into lines. Preserves empty trailing lines only if the
/// original content ends with a newline (tracked separately).
std::vector<std::string> splitLines(const std::string& text) {
    std::vector<std::string> lines;
    std::istringstream stream(text);
    std::string line;
    while (std::getline(stream, line))
        lines.push_back(line);
    return lines;
}

/// Count leading whitespace columns (spaces count as 1, tabs as 4).
int indentLevel(const std::string& line) {
    int level = 0;
    for (char c : line) {
        if (c == ' ')
            ++level;
        else if (c == '\t')
            level += 4;
        else
            break;
    }
    return level;
}

/// Return true if a line is blank (empty or whitespace-only).
bool isBlank(const std::string& line) {
    return line.find_first_not_of(" \t") == std::string::npos;
}

/// Trim leading and trailing whitespace from a string.
std::string trim(const std::string& s) {
    size_t start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return {};
    size_t end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

/// Extract the return type annotation from the combined signature text.
/// Looks for `-> <type>` before the final `:`.
std::string extractReturnType(const std::string& sigText) {
    // Find the last ':' (the colon that ends the signature)
    auto colonPos = sigText.rfind(':');
    if (colonPos == std::string::npos) return {};

    // Search backwards from the colon for `->`
    auto arrowPos = sigText.rfind("->", colonPos);
    if (arrowPos == std::string::npos) return {};

    // The return type sits between `->` and `:`
    std::string raw = sigText.substr(arrowPos + 2, colonPos - (arrowPos + 2));
    return trim(raw);
}

/// Join lines back into a single string with newline separators.
/// Appends a trailing newline if `trailingNewline` is true.
std::string joinLines(const std::vector<std::string>& lines, bool trailingNewline) {
    std::string result;
    for (size_t i = 0; i < lines.size(); ++i) {
        if (i > 0) result += '\n';
        result += lines[i];
    }
    if (trailingNewline) result += '\n';
    return result;
}

} // anonymous namespace

StubResult PythonStubGenerator::stubFunction(const std::string& filePath, const std::string& funcName) {
    StubResult result;

    if (!readFile(filePath, result.originalContent)) {
        result.error = "failed to read file: " + filePath;
        return result;
    }

    const auto& content = result.originalContent;
    bool trailingNewline = !content.empty() && content.back() == '\n';

    std::vector<std::string> lines = splitLines(content);
    if (lines.empty()) {
        result.error = "empty file: " + filePath;
        return result;
    }

    // Build regex to match `def funcName(` or `async def funcName(`
    // Capture group 1: leading indentation
    std::regex defRe(R"(^(\s*)(?:async\s+)?def\s+)" + funcName + R"(\s*\()");

    // --- Locate the def line ---
    int defLineIdx = -1;
    std::string defIndentStr;
    for (int i = 0; i < static_cast<int>(lines.size()); ++i) {
        std::smatch m;
        if (std::regex_search(lines[i], m, defRe)) {
            defLineIdx = i;
            defIndentStr = m[1].str();
            break;
        }
    }

    if (defLineIdx < 0) {
        result.error = "function not found: " + funcName;
        return result;
    }

    int defIndent = static_cast<int>(defIndentStr.size());

    // --- Find the end of the signature (the line ending with ':') ---
    // Handle multi-line signatures by tracking parenthesis balance.
    int sigEndIdx = defLineIdx;
    {
        int parenDepth = 0;
        for (int i = defLineIdx; i < static_cast<int>(lines.size()); ++i) {
            for (char c : lines[i]) {
                if (c == '(')
                    ++parenDepth;
                else if (c == ')')
                    --parenDepth;
            }

            // Signature ends when parens are balanced and line ends with ':'
            std::string stripped = lines[i];
            while (!stripped.empty() && (stripped.back() == ' ' || stripped.back() == '\t'))
                stripped.pop_back();

            if (parenDepth <= 0 && !stripped.empty() && stripped.back() == ':') {
                sigEndIdx = i;
                break;
            }
        }
    }

    // --- Extract return type from the full signature text ---
    std::string sigText;
    for (int i = defLineIdx; i <= sigEndIdx; ++i) {
        if (i > defLineIdx) sigText += ' ';
        sigText += lines[i];
    }
    std::string returnType = extractReturnType(sigText);

    // --- Find the body range ---
    // Body: consecutive lines after the signature colon where each line is
    // either blank or has indentation strictly greater than defIndent.
    int bodyStart = sigEndIdx + 1;
    int bodyEnd = bodyStart; // exclusive

    for (int i = bodyStart; i < static_cast<int>(lines.size()); ++i) {
        if (isBlank(lines[i])) {
            bodyEnd = i + 1;
            continue;
        }
        if (indentLevel(lines[i]) > defIndent) {
            bodyEnd = i + 1;
        } else {
            break;
        }
    }

    // Trim trailing blank lines that belong between functions, not to the body.
    // Walk backwards from bodyEnd while the line is blank AND the next
    // non-blank line (if any) is at defIndent or less — those blanks separate
    // top-level definitions and should be preserved outside the stub.
    while (bodyEnd > bodyStart && isBlank(lines[bodyEnd - 1])) {
        --bodyEnd;
    }

    if (bodyEnd <= bodyStart) {
        result.error = "no function body found for: " + funcName;
        return result;
    }

    // --- Determine body indentation ---
    // Prefer the actual indentation of the first non-blank body line;
    // fall back to defIndent + 4.
    std::string bodyIndent(defIndent + 4, ' ');
    for (int i = bodyStart; i < bodyEnd; ++i) {
        if (!isBlank(lines[i])) {
            int lvl = indentLevel(lines[i]);
            bodyIndent = std::string(lvl, ' ');
            break;
        }
    }

    // --- Generate stub body based on return type ---
    std::string stubLine;
    if (returnType.empty() || returnType == "None") {
        stubLine = bodyIndent + "pass";
    } else if (returnType == "bool") {
        stubLine = bodyIndent + "return False";
    } else if (returnType == "int" || returnType == "float") {
        stubLine = bodyIndent + "return 0";
    } else if (returnType == "str") {
        stubLine = bodyIndent + "return \"\"";
    } else {
        stubLine = bodyIndent + "return None";
    }

    // --- Assemble the modified lines ---
    std::vector<std::string> newLines;
    newLines.reserve(lines.size());

    // Lines before body (includes the signature)
    for (int i = 0; i < bodyStart; ++i)
        newLines.push_back(lines[i]);

    // Stub replacement (single line)
    newLines.push_back(stubLine);

    // Lines after body
    for (int i = bodyEnd; i < static_cast<int>(lines.size()); ++i)
        newLines.push_back(lines[i]);

    std::string modified = joinLines(newLines, trailingNewline);

    if (!writeFile(filePath, modified)) {
        result.error = "failed to write modified file: " + filePath;
        return result;
    }

    result.success = true;
    return result;
}

bool PythonStubGenerator::restoreFile(const std::string& filePath, const StubResult& result) {
    if (result.originalContent.empty()) return false;
    return writeFile(filePath, result.originalContent);
}

} // namespace topo::check
