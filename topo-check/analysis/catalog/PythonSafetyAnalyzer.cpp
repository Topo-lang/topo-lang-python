// PythonSafetyAnalyzer -- L2 whitelist-based containment analysis for Python.
//
// Two back-ends selected at runtime from LSP server capabilities:
//
//   1. LSP semanticTokens + hover.  Used when `bridge_.hasSemanticTokens()`
//      is true (basedpyright, or any LSP that implements the protocol).
//
//   2. Python ast subprocess.  Used when the LSP does not implement
//      textDocument/semanticTokens/full — most notably stock pyright,
//      which returns -32601 Unhandled method.  The subprocess is
//      `topo_extract_python.py`, a stdlib-only Python script that walks
//      every source file with `ast` and emits resolved call sites.
//      This back-end gives full-fidelity L2 without depending on any
//      specific LSP, at the cost of not resolving type-dependent method
//      calls (e.g. `self.foo()` or `instance.bar()` where the instance
//      type is unknown).  That tradeoff is acceptable for the containment
//      use case because the safety catalog is keyed on module-qualified
//      names — dangerous APIs like os.system / subprocess.Popen / eval /
//      exec are always reachable through an ast dotted chain.

#include "PythonSafetyAnalyzer.h"
#include "PyrightBridge.h"
#include "PythonUnsafeCatalog.h"

#include "topo/Platform/Process.h"
#include "topo/Platform/ToolResolution.h"

#include <nlohmann/json.hpp>

#include <cstdlib>
#include <filesystem>
#include <set>
#include <string>

namespace fs = std::filesystem;

namespace {

/// Extract qualified name from Pyright hover markdown.
/// Pyright hover responses typically contain lines like:
///   "(function) math.sqrt(x: SupportsFloat) -> float"
///   "(class) collections.Counter"
///   "(method) list.append(object: _T) -> None"
///   "(variable) os.path: module"
///   "(module) json"
/// We extract the qualified dotted name after the type annotation
/// and convert "." to "::" for internal use.
std::string extractQualifiedName(const std::string& hover) {
    if (hover.empty()) return "";

    size_t kindEnd = hover.find(") ");
    size_t start = 0;
    if (kindEnd != std::string::npos) {
        start = kindEnd + 2;
    }

    size_t end = start;
    while (end < hover.size()) {
        char c = hover[end];
        if (c == '(' || c == '\n' || c == '\r') break;
        if (c == ':' && end + 1 < hover.size() && hover[end + 1] == ' ') break;
        ++end;
    }

    while (end > start && (hover[end - 1] == ' ' || hover[end - 1] == '\t')) {
        --end;
    }

    if (end <= start) return "";

    std::string dotted = hover.substr(start, end - start);
    return topo::check::PythonSafePatterns::dotToColonColon(dotted);
}

/// Convert `os::system` back to `os.system` for catalog lookup.
std::string colonColonToDot(const std::string& name) {
    std::string result = name;
    for (size_t i = 0; i < result.size(); ++i) {
        if (i + 1 < result.size() && result[i] == ':' && result[i + 1] == ':') {
            result.replace(i, 2, ".");
        }
    }
    return result;
}

/// Locate topo_extract_python.py alongside the source tree via the
/// TOPO_SOURCE_DIR compile-time define, with an env-var override for
/// deployed installations.  Returns an empty string if the script cannot
/// be found — callers must surface that as a fallback warning.
std::string resolveExtractorScript() {
    if (const char* envPath = std::getenv("TOPO_PYTHON_EXTRACTOR")) {
        if (*envPath && fs::exists(envPath)) return envPath;
    }
#ifdef TOPO_SOURCE_DIR
    fs::path candidate = fs::path(TOPO_SOURCE_DIR) / "topo-lang-python" /
                         "topo-check" / "extractor" / "topo_extract_python.py";
    if (fs::exists(candidate)) return candidate.string();
#endif
    return {};
}

} // anonymous namespace

namespace topo::check {

PythonSafetyAnalyzer::PythonSafetyAnalyzer(lsp::PyrightBridge& bridge,
                                           const PythonSafePatterns& patterns)
    : bridge_(bridge), patterns_(patterns) {}

CheckResult PythonSafetyAnalyzer::analyze(const SymbolTable& symbols,
                                          const std::vector<std::string>& sourceFiles,
                                          const ContainmentConfig& config) {
    CheckResult result;
    if (!config.isEnabled()) return result;
    if (!bridge_.isAvailable()) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "Pyright unavailable — falling back to L1 regex scanning";
        result.addDiagnostic(std::move(d));
        return result;
    }

    std::vector<DetectedCallSite> callSites;
    std::vector<HostImport> imports;

    // Dispatch: prefer LSP semantic tokens when the server supports them
    // (basedpyright), otherwise use the Python ast subprocess (stock
    // pyright).  Both back-ends funnel results into the same checkContainment
    // call below.
    bool usedAstBackend = false;
    int filesWithEmptyTokens = 0;
    int astFileCount = 0;

    // Emit a backend-selection Note up front so the user sees which L2
    // path was chosen before any per-file analysis runs. The Note carries
    // a stable `backend:` discriminator (`lsp-semantic-tokens` vs
    // `ast-subprocess`) that downstream tooling can branch on. The
    // ast-subprocess path is portable but static-import-only — instance
    // method calls collapse to attribute chains; the lsp path resolves
    // them but depends on the LSP binary implementing
    // textDocument/semanticTokens (basedpyright does; stock pyright does
    // not).
    {
        CheckDiagnostic d;
        d.severity = Severity::Note;
        d.check = "containment-l2-backend";
        d.message = std::string("L2 backend selected: ") +
            (bridge_.hasSemanticTokens()
                ? "lsp-semantic-tokens (LSP server implements "
                  "textDocument/semanticTokens; e.g. basedpyright). "
                  "Higher fidelity for instance-method call resolution, "
                  "depends on the LSP binary."
                : "ast-subprocess (stock pyright lacks "
                  "textDocument/semanticTokens; falling back to "
                  "topo_extract_python.py). Portable default, "
                  "static-import resolution only — instance-method "
                  "calls collapse to attribute chains.");
        result.addDiagnostic(std::move(d));
    }

    if (bridge_.hasSemanticTokens()) {
        for (const auto& file : sourceFiles) {
            if (!analyzeFileViaLSP(file, symbols, config, callSites)) {
                ++filesWithEmptyTokens;
            }
        }

        // Principle 16: if the LSP produced no tokens for any file, emit a
        // warning and return so CheckRunner falls through to L1.
        if (!sourceFiles.empty() &&
            filesWithEmptyTokens == static_cast<int>(sourceFiles.size())) {
            CheckDiagnostic d;
            d.severity = Severity::Warning;
            d.check = "containment-l2";
            d.message = "Pyright returned no semantic tokens for any of " +
                        std::to_string(sourceFiles.size()) +
                        " source file(s) — L2 cannot run, falling back to L1 "
                        "(pyright likely does not implement textDocument/semanticTokens; "
                        "consider basedpyright or another LSP)";
            result.addDiagnostic(std::move(d));
            return result;
        }
    } else {
        usedAstBackend = true;
        std::vector<CheckDiagnostic> subprocessDiagnostics;
        bool ok = analyzeViaAstSubprocess(sourceFiles, symbols, callSites,
                                          subprocessDiagnostics);
        for (auto& d : subprocessDiagnostics) result.addDiagnostic(std::move(d));
        if (!ok) {
            // Subprocess failed entirely — fall through so CheckRunner
            // reverts to L1.  The warning was already appended above.
            return result;
        }
        astFileCount = static_cast<int>(sourceFiles.size());
    }

    // Deduplicate: same file+line+callee call sites
    {
        std::set<std::pair<std::string, int>> seen;
        std::vector<DetectedCallSite> deduped;
        for (auto& site : callSites) {
            auto key = std::make_pair(site.file + "::" + site.calleePattern, site.line);
            if (seen.insert(key).second) {
                deduped.push_back(std::move(site));
            }
        }
        callSites = std::move(deduped);
    }

    // Use the standard containment check with L2-resolved call sites
    checkContainment(symbols, imports, callSites, config, result);

    // Surface partial-extraction warning for the LSP path when some (but
    // not all) files lacked tokens.  The ast subprocess path either
    // succeeds for all files or fails as a whole, so this branch is
    // LSP-only.
    if (!usedAstBackend && filesWithEmptyTokens > 0) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "Pyright returned no semantic tokens for " +
                    std::to_string(filesWithEmptyTokens) + " of " +
                    std::to_string(sourceFiles.size()) +
                    " source file(s) — those files were not analyzed at L2";
        result.addDiagnostic(std::move(d));
    }

    // Mark as real L2 result so CheckRunner does not fall through to L1.
    {
        int analyzedFileCount = usedAstBackend
            ? astFileCount
            : (static_cast<int>(sourceFiles.size()) - filesWithEmptyTokens);
        CheckDiagnostic d;
        d.severity = Severity::Note;
        d.check = "containment";
        d.message = std::string("L2 deep analysis completed (") +
                    (usedAstBackend ? "ast subprocess, " : "LSP semanticTokens, ") +
                    std::to_string(analyzedFileCount) + "/" +
                    std::to_string(sourceFiles.size()) + " file(s), " +
                    std::to_string(callSites.size()) + " call site(s))";
        result.addDiagnostic(std::move(d));
    }

    return result;
}

bool PythonSafetyAnalyzer::classifyCallSite(const std::string& dottedCallee,
                                             const std::string& caller,
                                             const std::string& file,
                                             int line,
                                             const SymbolTable& symbols,
                                             DetectedCallSite& out) const {
    if (dottedCallee.empty() || dottedCallee == "<unknown>") return false;

    // Normalize to :: separator for whitelist lookup.
    std::string qualifiedName = PythonSafePatterns::dotToColonColon(dottedCallee);

    // 1. Safe stdlib symbol → drop.
    if (patterns_.isStdlibSymbolSafe(qualifiedName)) return false;

    // 2. Safe construct (e.g. `print`, `len`) → drop.
    auto lastSep = qualifiedName.rfind("::");
    std::string simpleName = (lastSep != std::string::npos)
                             ? qualifiedName.substr(lastSep + 2)
                             : qualifiedName;
    if (patterns_.isConstructSafe(simpleName)) return false;

    // 3. Unsafe construct (eval, exec, __import__) is reported regardless
    //    of whether a project function happens to share the name.
    bool isUnsafeConstruct = patterns_.isConstructUnsafe(simpleName);

    // 4. Project-declared function (.topo) → drop unless it matches an
    //    unsafe construct.
    if (!isUnsafeConstruct) {
        bool isDeclared = false;
        for (const auto& [name, fn] : symbols.functions()) {
            if (fn.qualifiedName == qualifiedName ||
                fn.qualifiedName == dottedCallee ||
                fn.simpleName == simpleName) {
                isDeclared = true;
                break;
            }
        }
        if (isDeclared) return false;
    }

    // 5. Classify via the unsafe catalog (uses dot-separated form).
    auto catalogLevel = PythonUnsafeCatalog::classifyCall(dottedCallee);
    if (catalogLevel == UnsafeLevel::Safe) {
        // Retry with just the simple name for broader matching.
        catalogLevel = PythonUnsafeCatalog::classifyCall(simpleName);
    }
    if (catalogLevel == UnsafeLevel::Safe && !isUnsafeConstruct) {
        // Unresolved attribute chain or user method the catalog has
        // nothing to say about.  Do not report as a violation — the
        // L2 path is meant to raise signal, not noise.
        return false;
    }

    out.calleePattern = qualifiedName;
    out.callerQualifiedName = caller.empty() ? std::string("<module>") : caller;
    out.capability = std::nullopt;
    out.unsafeLevel = (catalogLevel != UnsafeLevel::Safe)
                      ? catalogLevel : UnsafeLevel::Escape;
    out.file = file;
    out.line = line;
    return true;
}

bool PythonSafetyAnalyzer::analyzeFileViaLSP(const std::string& filePath,
                                              const SymbolTable& symbols,
                                              const ContainmentConfig& /*config*/,
                                              std::vector<DetectedCallSite>& callSites) {
    bridge_.openDocument(filePath);
    struct DocGuard {
        lsp::PyrightBridge& b;
        const std::string& path;
        ~DocGuard() { b.closeDocument(path); }
    } guard{bridge_, filePath};

    auto tokens = bridge_.getSemanticTokens(filePath);
    if (tokens.empty()) {
        return false;
    }

    // Fetch the document outline once so every call site can be attributed
    // to its real enclosing function (L2 synthetic-caller attribution).
    // Python uses "::" as
    // the canonical separator inside the symbol table and "." as the LSP
    // display separator; we pass "::" so isExternalCaller's rfind("::")
    // simple-name fallback works.
    auto docSymbols = bridge_.getDocumentSymbols(filePath);

    for (const auto& token : tokens) {
        if (token.type != "function" && token.type != "method") continue;
        if (token.modifiers.find("declaration") != std::string::npos ||
            token.modifiers.find("definition") != std::string::npos) continue;

        auto hover = bridge_.getHoverAt(filePath, token.line, token.column);
        if (!hover) continue;

        std::string qualifiedName = extractQualifiedName(*hover);
        if (qualifiedName.empty()) continue;

        // classifyCallSite expects dotted form.
        std::string dotted = colonColonToDot(qualifiedName);

        std::string callerQN = lsp::LSPBridge::findEnclosingFunction(
            docSymbols, token.line, "::");
        if (callerQN.empty()) {
            callerQN = "<l2:" + filePath + ":" +
                       std::to_string(token.line + 1) + ">";
        }

        DetectedCallSite site;
        if (classifyCallSite(dotted, callerQN, filePath,
                             token.line + 1, symbols, site)) {
            callSites.push_back(std::move(site));
        }
    }
    return true;
}

bool PythonSafetyAnalyzer::analyzeViaAstSubprocess(
    const std::vector<std::string>& sourceFiles,
    const SymbolTable& symbols,
    std::vector<DetectedCallSite>& callSites,
    std::vector<CheckDiagnostic>& diagnostics) {

    std::string scriptPath = resolveExtractorScript();
    if (scriptPath.empty()) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "topo_extract_python.py not found — L2 ast subprocess "
                    "path unavailable (set TOPO_PYTHON_EXTRACTOR to override)";
        diagnostics.push_back(std::move(d));
        return false;
    }

    // Resolve a Python 3 interpreter via the cross-platform helper.
    // ``"python3"`` is not a valid binary name on Windows (Microsoft ships
    // ``python.exe`` and the ``py`` launcher only), so a hard-coded literal
    // makes L2 silently fall back to L1 on Windows with a meaningless
    // "exit code 1" diagnostic. The helper honours ``TOPO_PYTHON`` for
    // out-of-PATH installs.
    std::vector<std::string> interp = platform::findPythonInterpreter();
    if (interp.empty()) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "No Python interpreter found on PATH for L2 ast subprocess "
                    "(probed python3, python, py -3). Install Python 3 or set "
                    "TOPO_PYTHON to the interpreter path (e.g. "
                    "TOPO_PYTHON=C:\\Python311\\python.exe).";
        diagnostics.push_back(std::move(d));
        return false;
    }

    std::vector<std::string> args;
    args.reserve(interp.size() + sourceFiles.size());
    // findPythonInterpreter() returns the executable as element 0 and
    // any leading flags (e.g. ``py -3``) as element 1+; the first arg
    // beyond that is the extractor script.
    for (size_t i = 1; i < interp.size(); ++i) args.push_back(interp[i]);
    args.push_back(scriptPath);
    for (const auto& f : sourceFiles) args.push_back(f);

    // 30s timeout is generous for pure ast parsing of a project.
    auto result = platform::runProcessCaptureWithTimeout(interp[0], args, 30000);
    if (result.exitCode != 0) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "topo_extract_python.py (" + interp[0] + ") exited with code " +
                    std::to_string(result.exitCode) +
                    (result.stderrOutput.empty() ? std::string{}
                                                  : (": " + result.stderrOutput));
        diagnostics.push_back(std::move(d));
        return false;
    }

    nlohmann::json response;
    try {
        response = nlohmann::json::parse(result.stdoutOutput);
    } catch (const nlohmann::json::exception& e) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = std::string("topo_extract_python.py emitted invalid JSON: ") +
                    e.what();
        diagnostics.push_back(std::move(d));
        // Stderr is the only remaining signal in this failure path;
        // forward it unchanged so a user sees both halves.
        if (!result.stderrOutput.empty()) {
            CheckDiagnostic s;
            s.severity = Severity::Warning;
            s.check = "containment-l2";
            s.message = "topo_extract_python.py stderr: " + result.stderrOutput;
            diagnostics.push_back(std::move(s));
        }
        return false;
    }

    if (!response.contains("callSites") || !response["callSites"].is_array()) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "topo_extract_python.py response missing callSites array";
        diagnostics.push_back(std::move(d));
        return false;
    }

    // Per-file parse / read errors. The extractor now emits a structured
    // ``fileErrors`` array (so a SyntaxError in one file no longer drops
    // silently out of the containment verdict)
    // alongside the call sites; each entry becomes its own Warning
    // diagnostic so a user sees exactly which files dropped out of L2
    // coverage and why. The LSP semanticTokens path already had this
    // visibility (see ``filesWithEmptyTokens`` summary above); this branch
    // restores symmetry for the ast-subprocess backend.
    int skippedFileCount = 0;
    if (response.contains("fileErrors") &&
        response["fileErrors"].is_array()) {
        for (const auto& entry : response["fileErrors"]) {
            if (!entry.is_object()) continue;
            std::string file = entry.value("file", "<unknown>");
            std::string kind = entry.value("kind", "error");
            std::string msg = entry.value("message", "");
            int line = entry.value("line", 0);

            CheckDiagnostic d;
            d.severity = Severity::Warning;
            d.check = "containment-l2";
            d.message = "topo_extract_python.py " + kind + " in " + file +
                        (line > 0 ? (":" + std::to_string(line)) : std::string{}) +
                        ": " + msg + " (file dropped from L2 coverage)";
            diagnostics.push_back(std::move(d));
            ++skippedFileCount;
        }
    } else if (!result.stderrOutput.empty()) {
        // Legacy fallback for older extractors (or third-party drop-in
        // replacements) that did not emit structured ``fileErrors``.
        // Surface the stderr blob as a single Warning so the failure
        // does not vanish silently.
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "topo_extract_python.py reported: " + result.stderrOutput;
        diagnostics.push_back(std::move(d));
    }

    if (skippedFileCount > 0) {
        CheckDiagnostic d;
        d.severity = Severity::Warning;
        d.check = "containment-l2";
        d.message = "L2 ast subprocess: " +
                    std::to_string(static_cast<int>(sourceFiles.size()) -
                                   skippedFileCount) +
                    " of " + std::to_string(sourceFiles.size()) +
                    " file(s) analysed, " + std::to_string(skippedFileCount) +
                    " skipped (see preceding diagnostics)";
        diagnostics.push_back(std::move(d));
    }

    for (const auto& entry : response["callSites"]) {
        if (!entry.is_object()) continue;
        std::string file = entry.value("file", "");
        int line = entry.value("line", 0);
        std::string callee = entry.value("callee", "");
        std::string caller = entry.value("caller", "");

        DetectedCallSite site;
        if (classifyCallSite(callee, caller, file, line, symbols, site)) {
            callSites.push_back(std::move(site));
        }
    }
    return true;
}

} // namespace topo::check
