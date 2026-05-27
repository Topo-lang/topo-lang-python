#ifndef TOPO_CHECK_PYTHONCALLSITEEXTRACTOR_H
#define TOPO_CHECK_PYTHONCALLSITEEXTRACTOR_H

#include "topo/Check/CallSiteExtractor.h"

#include <string>
#include <vector>

namespace topo::check {

/// Extracts external API call sites from Python source files using
/// regex-based scanning with indentation-level scope tracking.
/// Uses the same scope-tracking approach as PythonSymbolExtractor:
/// indent level determines scope entry/exit instead of braces.
class PythonCallSiteExtractor : public CallSiteExtractor {
public:
    /// Extract call sites matching the CapabilityCatalog API list from a single file.
    std::vector<DetectedCallSite> extractCallSites(const std::string& filePath) override;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONCALLSITEEXTRACTOR_H
