#ifndef TOPO_CHECK_PYTHONLSPUTILS_H
#define TOPO_CHECK_PYTHONLSPUTILS_H

#include <cctype>
#include <string>

namespace topo::check {

/// Extract qualified name from Pyright hover markdown.
///
/// Input examples:
///   "(function) os.path.join(*paths: str) -> str"  -> "os::path::join"
///   "(class) pathlib.Path"                         -> "pathlib::Path"
///   "(method) list.append(object: _T) -> None"     -> "list::append"
///   "(function) open(file: ...) -> IO"             -> "open"
///   "(variable) os.sep: str"                       -> "os::sep"
///
/// Pyright hover text starts with a kind tag "(function)", "(class)", etc.
/// followed by the dotted qualified name and optional signature.
/// Python uses '.' as module separator; this function converts to '::' for
/// Topo internal format.
inline std::string extractQualifiedName(const std::string& hover) {
    // Skip leading whitespace
    size_t pos = 0;
    while (pos < hover.size() && std::isspace(static_cast<unsigned char>(hover[pos]))) ++pos;

    // Skip kind tag: "(function)", "(class)", "(method)", "(variable)", "(module)", etc.
    if (pos < hover.size() && hover[pos] == '(') {
        auto closeTag = hover.find(')', pos);
        if (closeTag != std::string::npos) {
            pos = closeTag + 1;
            // Skip whitespace after tag
            while (pos < hover.size() && std::isspace(static_cast<unsigned char>(hover[pos]))) ++pos;
        }
    }

    // Now extract the qualified name (dotted identifier before '(' or ':' or end)
    size_t nameStart = pos;
    size_t nameEnd = pos;
    while (nameEnd < hover.size()) {
        char c = hover[nameEnd];
        if (std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '.') {
            ++nameEnd;
        } else {
            break;
        }
    }

    if (nameStart == nameEnd) return "";

    std::string dottedName = hover.substr(nameStart, nameEnd - nameStart);

    // Strip leading/trailing dots
    while (!dottedName.empty() && dottedName.front() == '.') dottedName = dottedName.substr(1);
    while (!dottedName.empty() && dottedName.back() == '.') dottedName.pop_back();

    if (dottedName.empty()) return "";

    // Convert '.' to '::' for Topo internal format
    std::string qualified;
    for (size_t i = 0; i < dottedName.size(); ++i) {
        if (dottedName[i] == '.') {
            qualified += "::";
        } else {
            qualified += dottedName[i];
        }
    }
    return qualified;
}

/// Convert a Python dotted name (e.g. "os.path.join") to Topo internal
/// format using '::' separator (e.g. "os::path::join").
inline std::string dotToColonColon(const std::string& dottedName) {
    std::string result;
    for (char c : dottedName) {
        if (c == '.') {
            result += "::";
        } else {
            result += c;
        }
    }
    return result;
}

/// Determine whether a semantic token modifier string contains the given modifier.
/// Modifier strings from Pyright are comma-separated, e.g. "declaration,readonly".
inline bool hasModifier(const std::string& modifiers, const std::string& modifier) {
    return modifiers.find(modifier) != std::string::npos;
}

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONLSPUTILS_H
