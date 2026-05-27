#ifndef TOPO_CHECK_PYTHONIMPORTEXTRACTOR_H
#define TOPO_CHECK_PYTHONIMPORTEXTRACTOR_H

#include "topo/Check/ImportExtractor.h"

#include <string>
#include <vector>

namespace topo::check {

/// Extracts import statements from Python source files.
/// Handles `import X`, `from X import Y`, and dotted modules.
/// For dotted modules, uses the top-level component for classification
/// (e.g. `from os.path import join` -> normalizedPath = "os").
class PythonImportExtractor : public ImportExtractor {
public:
    /// Extract all import paths from a single file.
    std::vector<HostImport> extractImports(const std::string& filePath) override;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONIMPORTEXTRACTOR_H
