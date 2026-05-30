#include "PyrightBridge.h"

#include "topo/Platform/Platform.h"

#include <fstream>
#include <regex>
#include <string>
#include <vector>

namespace topo::lsp {

PyrightBridge::PyrightBridge() : LSPBridge("[pyright] ") {}

bool PyrightBridge::start(const std::string& rootUri) {
    return start(std::string{}, rootUri);
}

bool PyrightBridge::start(const std::string& pyrightPath, const std::string& rootUri) {
    namespace plat = topo::platform;

    std::string exe = pyrightPath;
    if (exe.empty()) {
        // Try common name for Pyright Language Server
        exe = "pyright-langserver" + std::string(plat::ExeSuffix);
    }

    std::vector<std::string> args = {"--stdio"};
    if (!startProcess(exe, args, rootUri))
        return false;

    parseSemanticTokenLegend();
    return true;
}

std::optional<SymbolResult> PyrightBridge::findDefinition(const std::string& qualifiedName,
                                                          const std::vector<std::string>& /*pythonFiles*/) {
    if (!isAvailable()) return std::nullopt;
    return queryWorkspaceSymbol(qualifiedName);
}

std::vector<SymbolResult> PyrightBridge::findReferences(const std::string& qualifiedName,
                                                        const std::vector<std::string>& /*pythonFiles*/) {
    if (!isAvailable()) return {};

    auto defn = queryWorkspaceSymbol(qualifiedName);
    if (!defn) return {};

    json params = {{"textDocument", {{"uri", pathToUri(defn->file)}}},
                   {"position", {{"line", defn->line}, {"character", defn->column}}},
                   {"context", {{"includeDeclaration", true}}}};

    auto response = sendRequest("textDocument/references", params);
    if (!response || !response->is_array()) return {};

    std::vector<SymbolResult> results;
    for (const auto& loc : *response) {
        SymbolResult r;
        r.file = uriToPath(loc["uri"].get<std::string>());
        r.line = loc["range"]["start"]["line"].get<int>();
        r.column = loc["range"]["start"]["character"].get<int>();
        results.push_back(std::move(r));
    }
    return results;
}

std::optional<std::string> PyrightBridge::getHoverInfo(const std::string& qualifiedName,
                                                       const std::vector<std::string>& /*pythonFiles*/) {
    if (!isAvailable()) return std::nullopt;

    auto defn = queryWorkspaceSymbol(qualifiedName);
    if (!defn) return std::nullopt;

    json params = {{"textDocument", {{"uri", pathToUri(defn->file)}}},
                   {"position", {{"line", defn->line}, {"character", defn->column}}}};

    auto response = sendRequest("textDocument/hover", params);
    if (!response || response->is_null()) return std::nullopt;

    if (response->contains("contents")) {
        const auto& contents = (*response)["contents"];
        if (contents.is_string()) {
            return contents.get<std::string>();
        }
        if (contents.is_object() && contents.contains("value")) {
            return contents["value"].get<std::string>();
        }
    }
    return std::nullopt;
}

// Escape ECMAScript regex metacharacters in ``s``. ``typeName`` arrives from
// .topo declaration callers and is documented as the symbol declaration name
// — not a regex pattern. Concatenating it raw into a ``std::regex`` literal
// is a regex-injection hazard:
// a dotted name like ``module.Foo`` turns ``.`` into "any character" and
// produces false matches; a bracketed name like ``Container[T]`` opens a
// malformed character class and throws ``std::regex_error`` straight through
// the caller. The fix escapes every ECMAScript metacharacter so the regex
// surface only matches the literal name.
static std::string escapeRegexLiteral(const std::string& s) {
    static const std::string meta = R"(\^$.|?*+()[]{})";
    std::string out;
    out.reserve(s.size() + 4);
    for (char c : s) {
        if (meta.find(c) != std::string::npos) out.push_back('\\');
        out.push_back(c);
    }
    return out;
}

std::optional<SymbolResult> PyrightBridge::findTypeDefinition(const std::string& typeName,
                                                              const std::vector<std::string>& sourceFiles,
                                                              const std::vector<std::string>& /*includeDirs*/) {
    // Prefer the live index when Pyright is running.
    if (isAvailable()) {
        auto result = queryWorkspaceSymbol(typeName);
        if (result) return result;
    }

    // Fallback: scan .py source files for a matching class definition.
    // Follow-set:
    //   - whitespace          → no inheritance / type params
    //   - '('                 → inheritance list `class Foo(Bar):`
    //   - ':'                 → no-base form `class Foo:`
    //   - '['                 → PEP 695 generic type params
    //                          `class Container[T]: ...` (Python 3.12+),
    //                          also covers PEP 696 defaults / bounds
    //                          (`class Foo[T = int]:`, `class Foo[T: Bound]:`)
    // The `[` is escaped as `\[` inside the character class to avoid
    // turning it into a (malformed) nested class.
    //
    // typeName is escaped (so a hostile type name can't inject regex
    // metacharacters into the class-definition pattern) and
    // regex construction is try/caught so an unforeseen pattern shape
    // degrades to "type not found" rather than throwing through the LSP
    // request handler.
    std::optional<std::regex> pattern;
    try {
        pattern.emplace(R"(^\s*class\s+)" +
                        escapeRegexLiteral(typeName) +
                        R"([\s(:\[])");
    } catch (const std::regex_error&) {
        return std::nullopt;
    }

    for (const auto& filePath : sourceFiles) {
        const std::string suffix = ".py";
        if (filePath.size() < suffix.size() || filePath.substr(filePath.size() - suffix.size()) != suffix) continue;

        std::ifstream file(filePath);
        if (!file.is_open()) continue;

        std::string line;
        int lineNo = 0;
        while (std::getline(file, line)) {
            ++lineNo;
            try {
                if (std::regex_search(line, *pattern)) {
                    return SymbolResult{filePath, lineNo, 0};
                }
            } catch (const std::regex_error&) {
                // Search-time error (eg. stack overflow on a pathological
                // line) — skip the line and keep scanning. Matches the
                // existing "regex_error -> type not found" policy above.
                continue;
            }
        }
    }

    return std::nullopt;
}

} // namespace topo::lsp
