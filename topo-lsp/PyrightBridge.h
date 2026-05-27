#ifndef TOPO_LSP_PYRIGHTBRIDGE_H
#define TOPO_LSP_PYRIGHTBRIDGE_H

#include "topo/LSP/LSPBridge.h"

namespace topo::lsp {

class PyrightBridge : public LSPBridge {
public:
    PyrightBridge();
    bool start(const std::string& rootUri) override;
    std::string displayName() const override { return "Python"; }

    bool start(const std::string& pyrightPath, const std::string& rootUri);
    std::optional<SymbolResult> findDefinition(const std::string& qualifiedName,
                                               const std::vector<std::string>& pythonFiles) override;
    std::vector<SymbolResult> findReferences(const std::string& qualifiedName,
                                             const std::vector<std::string>& pythonFiles) override;
    std::optional<std::string> getHoverInfo(const std::string& qualifiedName,
                                            const std::vector<std::string>& pythonFiles) override;

    /// Find host-language type definition for a named type.
    /// Queries Pyright workspace index first; falls back to scanning sourceFiles
    /// (.py) for class definitions matching typeName.
    std::optional<SymbolResult> findTypeDefinition(const std::string& typeName,
                                                   const std::vector<std::string>& sourceFiles,
                                                   const std::vector<std::string>& includeDirs) override;

    std::string languageId() const override { return "python"; }
};

} // namespace topo::lsp
#endif
