#ifndef TOPO_CHECK_PYTHONSYMBOLEXTRACTOR_H
#define TOPO_CHECK_PYTHONSYMBOLEXTRACTOR_H

#include "topo/Check/SymbolExtractor.h"

#include <string>
#include <vector>

namespace topo::check {

/// Regex-based L1 Python symbol extractor using indentation scope tracking.
///
/// Provides a safety-net fallback when Pyright LSP is unavailable.
/// Extracts classes, functions, and methods by parsing def/class statements
/// and tracking Python's indentation-based scope.
///
/// Design: false positives acceptable, false negatives are safety issues.
class PythonSymbolExtractor : public SymbolExtractor {
public:
    std::vector<HostSymbol> extractSymbols(const std::string& filePath) override;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONSYMBOLEXTRACTOR_H
