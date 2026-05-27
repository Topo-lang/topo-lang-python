#ifndef TOPO_CHECK_PYTHONLSPCALLSITEEXTRACTOR_H
#define TOPO_CHECK_PYTHONLSPCALLSITEEXTRACTOR_H

#include "PythonCallSiteExtractor.h"

#include <string>
#include <vector>

// Forward declaration
namespace topo::lsp { class PyrightBridge; }

namespace topo::check {

/// LSP-based Python call site extractor using Pyright semantic tokens and hover.
///
/// Extracts function/method call references by:
/// 1. Opening the document for Pyright analysis
/// 2. Requesting semantic tokens to find call references (non-declaration tokens)
/// 3. Using hover to resolve qualified callee names
/// 4. Classifying each callee via PythonUnsafeCatalog
///
/// This extractor handles what regex cannot: qualified name resolution across
/// modules, aliased imports, and dynamic attribute access.
class PythonLSPCallSiteExtractor {
public:
    explicit PythonLSPCallSiteExtractor(lsp::PyrightBridge& bridge);

    /// Extract call sites from a single source file.
    /// Returns only function/method call references with resolved qualified names.
    std::vector<DetectedCallSite> extractCallSites(const std::string& filePath);

private:
    lsp::PyrightBridge& bridge_;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONLSPCALLSITEEXTRACTOR_H
