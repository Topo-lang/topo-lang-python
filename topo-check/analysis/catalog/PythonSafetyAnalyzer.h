#ifndef TOPO_CHECK_PYTHONSAFETYANALYZER_H
#define TOPO_CHECK_PYTHONSAFETYANALYZER_H

#include "PythonSafePatterns.h"
#include "topo/Check/ContainmentCheck.h"

#include <string>
#include <vector>

// Forward declarations
namespace topo::lsp { class PyrightBridge; }
namespace topo { class SymbolTable; }

namespace topo::check {

/// L2 whitelist-based containment analyzer for Python.
///
/// Two back-ends, selected at runtime from server capabilities:
///   1. Pyright semantic tokens + hover (only works with basedpyright or any
///      LSP that implements textDocument/semanticTokens/full — stock pyright
///      does NOT implement this method and returns -32601).
///   2. Python ast subprocess (topo_extract_python.py). Uses the stdlib ast
///      module to walk every source file and emit call sites with resolved
///      dotted callees and enclosing caller names. Works against any pyright
///      version because it does not depend on the LSP at all.
///
/// The subprocess path is the default when semantic tokens are unavailable;
/// it gives full-fidelity L2 for the containment use case (module-qualified
/// stdlib calls) without adding a runtime dependency on basedpyright.
class PythonSafetyAnalyzer {
public:
    PythonSafetyAnalyzer(lsp::PyrightBridge& bridge, const PythonSafePatterns& patterns);

    /// Analyze source files for containment violations.
    /// Non-external functions calling non-whitelisted targets are reported.
    CheckResult analyze(const SymbolTable& symbols,
                        const std::vector<std::string>& sourceFiles,
                        const ContainmentConfig& config);

private:
    /// LSP semanticTokens path (basedpyright etc.).
    /// Returns true if the bridge produced tokens for this file, false if
    /// the token list came back empty so the caller can surface a visible
    /// fallback warning (principle 16).
    bool analyzeFileViaLSP(const std::string& filePath,
                           const SymbolTable& symbols,
                           const ContainmentConfig& config,
                           std::vector<DetectedCallSite>& callSites);

    /// Python ast subprocess path (stock pyright fallback).
    /// Spawns topo_extract_python.py with all sourceFiles as argv, parses
    /// the JSON response into DetectedCallSite entries, and filters them
    /// through the same safe-pattern / catalog logic used by the LSP path.
    /// Returns true if the subprocess ran and produced a valid response
    /// (even if zero call sites were emitted). Populates `diagnostics`
    /// with any warnings produced while parsing.
    bool analyzeViaAstSubprocess(const std::vector<std::string>& sourceFiles,
                                 const SymbolTable& symbols,
                                 std::vector<DetectedCallSite>& callSites,
                                 std::vector<CheckDiagnostic>& diagnostics);

    /// Shared classification: given a resolved dotted callee and its caller
    /// name, decide whether to emit a DetectedCallSite (returns true and
    /// fills `out`) or skip (returns false). This is the logic the two
    /// back-ends share so that whitelist/construct/declared-function
    /// handling stays in one place.
    bool classifyCallSite(const std::string& dottedCallee,
                          const std::string& caller,
                          const std::string& file,
                          int line,
                          const SymbolTable& symbols,
                          DetectedCallSite& out) const;

    lsp::PyrightBridge& bridge_;
    const PythonSafePatterns& patterns_;
};

} // namespace topo::check

#endif // TOPO_CHECK_PYTHONSAFETYANALYZER_H
