#ifndef TOPO_CHECK_PYTHONLSPIMPORTEXTRACTOR_H
#define TOPO_CHECK_PYTHONLSPIMPORTEXTRACTOR_H

#include "PythonImportExtractor.h"

#include <string>
#include <vector>

namespace topo::check {

/// Clean line-based Python import extractor.
///
/// Python imports are deterministic syntax -- no need for LSP.
/// This extractor does the same job as PythonImportExtractor but with
/// a cleaner, more maintainable implementation:
///   - Direct string matching instead of regex
///   - Same multiline-string state machine
///   - PythonUnsafeCatalog classification for each import
///
/// Functionally equivalent to PythonImportExtractor.
class PythonLSPImportExtractor {
public:
    /// Extract all import paths from a single file.
    std::vector<HostImport> extractImports(const std::string& filePath);

    /// Extract imports from multiple files.
    std::vector<HostImport> extractAll(const std::vector<std::string>& files);
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONLSPIMPORTEXTRACTOR_H
