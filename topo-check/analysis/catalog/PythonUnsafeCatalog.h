#ifndef TOPO_CHECK_PYTHONUNSAFECATALOG_H
#define TOPO_CHECK_PYTHONUNSAFECATALOG_H

#include "topo/Check/CapabilityCatalog.h"
#include <string>

namespace topo::check {

/// Python unsafe behavior catalog.
/// Classifies Python patterns by unsafe level.
/// Level 1 (System): os, io, socket, subprocess
/// Level 2 (Dep): third-party packages (non-stdlib)
/// Level 3 (Input): web framework request objects
/// Level 4 (Escape): exec/eval, ctypes, pickle
class PythonUnsafeCatalog {
public:
    /// Classify a call site pattern (function name or qualified call).
    static UnsafeLevel classifyCall(const std::string& pattern);

    /// Classify an import path (module name).
    static UnsafeLevel classifyImport(const std::string& path);
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONUNSAFECATALOG_H
