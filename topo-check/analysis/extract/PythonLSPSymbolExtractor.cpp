// PythonLSPSymbolExtractor -- LSP-based Python symbol extraction via Pyright.
//
// Uses semantic tokens to find declaration/definition tokens, then
// resolves each via hover to get qualified names and signatures.

#include "PythonLSPSymbolExtractor.h"
#include "PythonLSPUtils.h"
#include "PyrightBridge.h"

#include <cctype>
#include <string>
#include <vector>

namespace topo::check {

PythonLSPSymbolExtractor::PythonLSPSymbolExtractor(lsp::PyrightBridge& bridge)
    : bridge_(bridge) {}

std::string PythonLSPSymbolExtractor::parseReturnType(const std::string& hover) {
    // Pyright hover format: "(function) name(params) -> ReturnType"
    // Find " -> " after the closing paren
    auto arrowPos = hover.rfind(" -> ");
    if (arrowPos == std::string::npos) return "";

    std::string retType = hover.substr(arrowPos + 4);

    // Trim whitespace
    auto first = retType.find_first_not_of(" \t\n");
    if (first == std::string::npos) return "";
    auto last = retType.find_last_not_of(" \t\n");
    return retType.substr(first, last - first + 1);
}

std::vector<std::string> PythonLSPSymbolExtractor::parseParamTypes(const std::string& hover) {
    auto openParen = hover.find('(', hover.find(')') == 0 ? 0 : hover.find(' '));
    // Find the first ( that is part of the parameter list, not the kind tag
    // Kind tag: "(function) name(" -- skip the kind tag paren
    size_t searchStart = 0;
    if (!hover.empty() && hover[0] == '(') {
        auto closeTag = hover.find(')');
        if (closeTag != std::string::npos) {
            searchStart = closeTag + 1;
        }
    }

    openParen = hover.find('(', searchStart);
    auto closeParen = hover.rfind(')');
    if (openParen == std::string::npos || closeParen == std::string::npos ||
        closeParen <= openParen) {
        return {};
    }

    std::string params = hover.substr(openParen + 1, closeParen - openParen - 1);

    // Trim
    auto start = params.find_first_not_of(" \t");
    if (start == std::string::npos) return {};
    params = params.substr(start);
    auto end = params.find_last_not_of(" \t");
    params = params.substr(0, end + 1);

    if (params.empty()) return {};

    // Split by comma, respecting brackets and parentheses
    std::vector<std::string> parts;
    int depth = 0;
    std::string current;
    for (char c : params) {
        if (c == '(' || c == '[' || c == '{')
            ++depth;
        else if (c == ')' || c == ']' || c == '}')
            --depth;
        else if (c == ',' && depth == 0) {
            parts.push_back(current);
            current.clear();
            continue;
        }
        current += c;
    }
    if (!current.empty()) parts.push_back(current);

    std::vector<std::string> types;
    for (auto& p : parts) {
        // Trim each param
        auto s = p.find_first_not_of(" \t");
        if (s == std::string::npos) continue;
        auto e = p.find_last_not_of(" \t");
        p = p.substr(s, e - s + 1);

        // Skip self and cls parameters
        if (p == "self" || p == "cls") continue;

        // Strip default value: "x: int = 5" -> "x: int"
        // Need to handle nested brackets in defaults
        int defaultDepth = 0;
        size_t eqPos = std::string::npos;
        for (size_t i = 0; i < p.size(); ++i) {
            if (p[i] == '(' || p[i] == '[' || p[i] == '{')
                ++defaultDepth;
            else if (p[i] == ')' || p[i] == ']' || p[i] == '}')
                --defaultDepth;
            else if (p[i] == '=' && defaultDepth == 0) {
                eqPos = i;
                break;
            }
        }
        if (eqPos != std::string::npos) {
            p = p.substr(0, eqPos);
            auto trimEnd = p.find_last_not_of(" \t");
            if (trimEnd != std::string::npos) p = p.substr(0, trimEnd + 1);
        }

        // Skip bare "self" or "cls" with annotation
        auto colonPos = p.find(':');
        if (colonPos != std::string::npos) {
            std::string name = p.substr(0, colonPos);
            auto ns = name.find_first_not_of(" \t");
            auto ne = name.find_last_not_of(" \t");
            if (ns != std::string::npos) name = name.substr(ns, ne - ns + 1);

            if (name == "self" || name == "cls") continue;

            std::string type = p.substr(colonPos + 1);
            auto ts = type.find_first_not_of(" \t");
            if (ts != std::string::npos) {
                auto te = type.find_last_not_of(" \t");
                type = type.substr(ts, te - ts + 1);
                types.push_back(type);
            }
        }
        // No annotation -- skip (no type info available)
    }

    return types;
}

std::string PythonLSPSymbolExtractor::detectEnclosingClass(const std::string& qualifiedName) {
    // "mod::Class::method" -> "mod::Class"
    auto lastSep = qualifiedName.rfind("::");
    if (lastSep == std::string::npos || lastSep == 0) return "";
    return qualifiedName.substr(0, lastSep);
}

Visibility PythonLSPSymbolExtractor::pythonVisibility(const std::string& name) {
    // Dunder methods: __init__, __str__, etc. -> Public
    if (name.size() >= 4 && name.substr(0, 2) == "__" && name.substr(name.size() - 2) == "__")
        return Visibility::Public;
    // Name-mangled: __private -> Private
    if (name.size() >= 2 && name.substr(0, 2) == "__") return Visibility::Private;
    // Convention-private: _private -> Private
    if (!name.empty() && name[0] == '_') return Visibility::Private;
    return Visibility::Public;
}

std::vector<HostSymbol> PythonLSPSymbolExtractor::extractSymbols(const std::string& filePath) {
    std::vector<HostSymbol> result;

    if (!bridge_.isAvailable()) return result;

    // 1. Open document for Pyright analysis
    bridge_.openDocument(filePath);
    struct DocGuard {
        lsp::PyrightBridge& b;
        const std::string& path;
        ~DocGuard() { b.closeDocument(path); }
    } guard{bridge_, filePath};

    // 2. Get semantic tokens
    auto tokens = bridge_.getSemanticTokens(filePath);
    if (tokens.empty()) {
        return result;
    }

    // 3. Filter and process declaration/definition tokens
    for (const auto& token : tokens) {
        // Only interested in declarations and definitions
        if (!hasModifier(token.modifiers, "declaration") &&
            !hasModifier(token.modifiers, "definition")) {
            continue;
        }

        // Map Pyright semantic token types to HostSymbolKind
        // Python has no struct/enum distinction -- only class and function
        bool isFunction = (token.type == "function");
        bool isMethod = (token.type == "method");
        bool isClass = (token.type == "class");

        if (!isFunction && !isMethod && !isClass) continue;

        // 4. Resolve via hover to get qualified name and signature
        auto hover = bridge_.getHoverAt(filePath, token.line, token.column);
        if (!hover) continue;

        std::string qualifiedName = extractQualifiedName(*hover);
        if (qualifiedName.empty()) continue;

        HostSymbol sym;
        sym.qualifiedName = qualifiedName;
        sym.file = filePath;
        sym.line = token.line + 1; // semantic tokens are 0-based, HostSymbol is 1-based

        // Extract simple name from qualified name
        auto lastSep = qualifiedName.rfind("::");
        sym.simpleName = (lastSep != std::string::npos)
                             ? qualifiedName.substr(lastSep + 2)
                             : qualifiedName;

        // Determine kind
        if (isClass) {
            sym.kind = HostSymbolKind::Class;
        } else if (isMethod) {
            // Check for static modifier
            if (hasModifier(token.modifiers, "static")) {
                sym.kind = HostSymbolKind::StaticMethod;
                sym.isStatic = true;
            } else {
                sym.kind = HostSymbolKind::Method;
            }

            // Detect constructor (__init__) and enclosing class
            std::string enclosing = detectEnclosingClass(qualifiedName);
            if (!enclosing.empty()) {
                sym.enclosingClass = enclosing;

                if (sym.simpleName == "__init__") {
                    sym.kind = HostSymbolKind::Constructor;
                }
            }

            // Parse return type and params from hover
            sym.returnType = parseReturnType(*hover);
            sym.paramTypes = parseParamTypes(*hover);
        } else if (isFunction) {
            sym.kind = HostSymbolKind::Function;
            sym.returnType = parseReturnType(*hover);
            sym.paramTypes = parseParamTypes(*hover);
        }

        // Visibility from Python naming convention
        sym.hostVisibility = pythonVisibility(sym.simpleName);

        result.push_back(std::move(sym));
    }

    return result;
}

} // namespace topo::check
