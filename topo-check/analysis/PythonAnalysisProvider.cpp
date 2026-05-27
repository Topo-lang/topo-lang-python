#include "PythonAnalysisProvider.h"
#include "PythonCallEdgeExtractor.h"
#include "PythonCallSiteExtractor.h"
#include "PythonImportExtractor.h"
#include "PythonLSPCallSiteExtractor.h"
#include "PythonLSPSymbolExtractor.h"
#include "PythonSafePatterns.h"
#include "PythonSafetyAnalyzer.h"
#include "PythonSymbolAccessExtractor.h"
#include "PythonSymbolExtractor.h"
#include "PyrightBridge.h"

#include <algorithm>
#include <filesystem>
#include <iostream>

namespace fs = std::filesystem;

namespace topo::check {

PythonAnalysisProvider::~PythonAnalysisProvider() {
    shutdownLSP();
}

std::unique_ptr<SymbolExtractor> PythonAnalysisProvider::createSymbolExtractor() {
    if (bridge_ && bridge_->isAvailable() && bridge_->hasSemanticTokens()) {
        return std::make_unique<PythonLSPSymbolExtractor>(*bridge_);
    }
    return std::make_unique<PythonSymbolExtractor>();  // L1 fallback
}

std::unique_ptr<ImportExtractor> PythonAnalysisProvider::createImportExtractor() {
    return std::make_unique<PythonImportExtractor>();  // Always regex — imports are simple
}

std::unique_ptr<CallSiteExtractor> PythonAnalysisProvider::createCallSiteExtractor() {
    return std::make_unique<PythonCallSiteExtractor>();  // L1 fallback
}

std::unique_ptr<CallEdgeExtractor> PythonAnalysisProvider::createCallEdgeExtractor() {
    // L1-only — no LSP/L2 merging layer for purity/visibility/stage-isolation.
    return std::make_unique<PythonCallEdgeExtractor>();
}

std::unique_ptr<SymbolAccessExtractor> PythonAnalysisProvider::createSymbolAccessExtractor() {
    return std::make_unique<PythonSymbolAccessExtractor>();
}

std::vector<std::string> PythonAnalysisProvider::collectSourceFiles(
    const std::string& projectDir,
    const std::vector<std::string>& /*includeDirs*/) const {
    std::vector<std::string> files;
    std::vector<fs::path> searchDirs = {
        fs::path(projectDir) / "src",
        fs::path(projectDir)};
    std::set<std::string> seen;
    for (const auto& dir : searchDirs) {
        if (!fs::exists(dir)) continue;
        for (const auto& entry : fs::recursive_directory_iterator(dir)) {
            if (entry.path().extension() == ".py") {
                std::string path = entry.path().string();
                if (seen.insert(path).second)
                    files.push_back(path);
            }
        }
    }
    std::sort(files.begin(), files.end());
    return files;
}

bool PythonAnalysisProvider::initLSP(const std::string& projectDir, bool verbose) {
    if (bridge_ && bridge_->isAvailable()) return true;

    auto bridge = std::make_unique<lsp::PyrightBridge>();
    std::string rootUri = "file://" + fs::canonical(projectDir).string();

    if (!bridge->start("", rootUri)) {
        return false;
    }

    if (!bridge->isAvailable()) {
        bridge->stop();
        return false;
    }

    if (!bridge->waitForIndex(std::chrono::milliseconds{30000})) {
        std::cerr << "[topo-lsp] Pyright index not ready after 30s, L2 analysis may be incomplete\n";
    }

    bridge_ = std::move(bridge);
    if (verbose) {
        std::cerr << "  PyrightBridge started\n";
    }
    return true;
}

void PythonAnalysisProvider::shutdownLSP() {
    if (bridge_) {
        bridge_->stop();
        bridge_.reset();
    }
}

bool PythonAnalysisProvider::isLSPReady() const {
    return bridge_ && bridge_->isAvailable();
}

std::optional<CheckResult> PythonAnalysisProvider::runDeepContainment(
    const SymbolTable& symbols,
    const std::vector<std::string>& sourceFiles,
    const ContainmentConfig& config,
    const std::string& projectDir,
    bool verbose) {
    CheckResult result;

    PythonSafePatterns patterns;
    if (!patterns.loadDefault()) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "PythonSafePatterns.toml not found — cannot run L2 analysis";
        result.addDiagnostic(std::move(d));
        return result;
    }

    if (!bridge_ || !bridge_->isAvailable()) {
        initLSP(projectDir, verbose);
    }
    if (!bridge_ || !bridge_->isAvailable()) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "Pyright unavailable — falling back to L1";
        result.addDiagnostic(std::move(d));
        return result;
    }

    PythonSafetyAnalyzer analyzer(*bridge_, patterns);
    result = analyzer.analyze(symbols, sourceFiles, config);
    return result;
}

std::unique_ptr<LanguageAnalysisProvider> createPythonAnalysisProvider() {
    return std::unique_ptr<LanguageAnalysisProvider>(new PythonAnalysisProvider());
}

} // namespace topo::check
