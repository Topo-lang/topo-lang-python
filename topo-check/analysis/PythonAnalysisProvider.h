#ifndef TOPO_CHECK_PYTHONANALYSISPROVIDER_H
#define TOPO_CHECK_PYTHONANALYSISPROVIDER_H

#include "topo/Check/LanguageAnalysisProvider.h"

#include <memory>
#include <string>

// Forward declaration
namespace topo::lsp { class PyrightBridge; }

namespace topo::check {

class PythonAnalysisProvider : public LanguageAnalysisProvider {
public:
    ~PythonAnalysisProvider() override;

    std::string separator() const override { return "."; }
    std::unique_ptr<SymbolExtractor> createSymbolExtractor() override;
    std::unique_ptr<ImportExtractor> createImportExtractor() override;
    std::unique_ptr<CallSiteExtractor> createCallSiteExtractor() override;
    std::unique_ptr<CallEdgeExtractor> createCallEdgeExtractor() override;
    std::unique_ptr<SymbolAccessExtractor> createSymbolAccessExtractor() override;
    std::vector<std::string> collectSourceFiles(
        const std::string& projectDir,
        const std::vector<std::string>& includeDirs) const override;

    std::optional<CheckResult> runDeepContainment(
        const SymbolTable& symbols,
        const std::vector<std::string>& sourceFiles,
        const ContainmentConfig& config,
        const std::string& projectDir,
        bool verbose) override;

    bool initLSP(const std::string& projectDir, bool verbose) override;
    void shutdownLSP() override;
    bool isLSPReady() const override;

private:
    std::unique_ptr<lsp::PyrightBridge> bridge_;
};

/// Factory function (avoids incomplete-type issues when constructing via make_unique).
std::unique_ptr<LanguageAnalysisProvider> createPythonAnalysisProvider();

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONANALYSISPROVIDER_H
