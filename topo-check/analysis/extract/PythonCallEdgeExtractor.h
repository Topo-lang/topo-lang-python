#ifndef TOPO_CHECK_PYTHONCALLEDGEEXTRACTOR_H
#define TOPO_CHECK_PYTHONCALLEDGEEXTRACTOR_H

#include "topo/Check/CallEdgeExtractor.h"

#include <string>
#include <vector>

namespace topo::check {

/// L1 regex-based Python call edge extractor used by StageIsolationCheck and
/// VisibilityCheck. Scans Python function bodies for `identifier(...)` and
/// `obj.method(...)` calls and emits caller→callee edges qualified by the
/// enclosing class scope (mirrors PythonCallSiteExtractor's indent-based
/// scope tracking).
///
/// Caller naming convention:
///   - Module-level function `foo()`        → caller = "foo"
///   - Method `Cls.method()`                → caller = "Cls.method"
///   - Nested function inside `foo()` body  → caller = "foo" (outer) for now
///
/// Callee naming convention:
///   - Bare call `bar(...)`                 → callee = "bar"
///   - Attribute call `obj.bar(...)`        → callee = "bar" (simple name)
///   - Module/class call `mod.bar(...)`     → callee = "mod.bar" (preserved)
class PythonCallEdgeExtractor : public CallEdgeExtractor {
public:
    std::vector<CallEdge> extractCallEdges(const std::string& filePath) override;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONCALLEDGEEXTRACTOR_H
