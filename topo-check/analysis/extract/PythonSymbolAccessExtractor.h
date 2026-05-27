#ifndef TOPO_CHECK_PYTHONSYMBOLACCESSEXTRACTOR_H
#define TOPO_CHECK_PYTHONSYMBOLACCESSEXTRACTOR_H

#include "topo/Check/SymbolAccessExtractor.h"

#include <string>
#include <vector>

namespace topo::check {

/// L1 regex-based Python symbol access extractor used by PurityCheck.
///
/// Two-pass strategy:
///   1. Scan the file once to collect module-level globals — assignments
///      that occur outside any `def` or `class` body. Examples:
///        counter = 0
///        NAMES = ["a", "b"]
///        TABLE: dict = {}
///   2. Inside function bodies, emit SymbolAccess{isWrite=true} for writes
///      to globals. Writes are detected by:
///        - Explicit `global X` declaration followed by any assignment to X
///          inside the same function body
///        - Bare `X = ...`, `X += ...`, `X[k] = ...`, `X.attr = ...` where
///          X is in the module-level globals set (conservative L1)
///
/// Reads are deferred to a later milestone — the load-bearing signal for
/// PurityCheck is writes in parallel stages. Member writes (`self.x = y`)
/// are NOT flagged as global writes.
class PythonSymbolAccessExtractor : public SymbolAccessExtractor {
public:
    std::vector<SymbolAccess> extractSymbolAccesses(const std::string& filePath) override;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONSYMBOLACCESSEXTRACTOR_H
