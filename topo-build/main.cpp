// topo-build-python -- Check-only backend for Python projects.
//
// Steps:
// 1. Parse BackendRequest JSON from argv[1]
// 2. Extract backendExtras: pythonPath, venvPath, topoCheckJobs
// 3. Collect .py source files
// 4. If !noVerify: run completeness check via PythonSymbolExtractor
// 5. Report diagnostics; exit 1 on error (unless warnOnly)

#include "topo/Build/BackendProtocol.h"
#include "analysis/extract/PythonSymbolExtractor.h"
#include "topo/Check/CheckTypes.h"
#include "topo/Check/CompletenessCheck.h"
#include "topo/Platform/ToolResolution.h"

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

namespace fs = std::filesystem;

// ============================================================
// backendExtras per-value validators (Python backend).
//
// Schema (all keys optional):
//   pythonPath     string   override Python interpreter path
//   venvPath       string   virtualenv root used during verbose logs
//   topoCheckJobs  integer  parallel extractor worker count (0=auto)
//
// Mirrors topo-build-llvm-cpp's pattern. Validation runs before
// any per-file extraction so a `"topoCheckJobs": "auto"` typo
// surfaces immediately rather than as a silent serial fallback.
// ============================================================

static bool expectStringIfPresent(const nlohmann::json& extras, const char* key) {
    if (!extras.contains(key)) return true;
    const auto& v = extras.at(key);
    if (!v.is_string()) {
        std::cerr << "error: backendExtras." << key
                  << ": expected string, got " << v.type_name() << "\n";
        return false;
    }
    return true;
}

static bool expectIntegerIfPresent(const nlohmann::json& extras, const char* key) {
    if (!extras.contains(key)) return true;
    const auto& v = extras.at(key);
    if (!v.is_number_integer()) {
        std::cerr << "error: backendExtras." << key
                  << ": expected integer, got " << v.type_name() << "\n";
        return false;
    }
    return true;
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <request.json>\n"
                  << "  Check-only backend invoked by topo-build. "
                     "Not intended for direct use.\n";
        return 1;
    }

    // --- Step 1: Parse backend request ---
    std::ifstream reqFile(argv[1]);
    if (!reqFile) {
        std::cerr << "error: cannot open '" << argv[1] << "'\n";
        return 1;
    }
    std::ostringstream buf;
    buf << reqFile.rdbuf();
    std::string reqJson = buf.str();
    reqFile.close();

    topo::build::BackendRequest req;
    if (!topo::build::deserializeBackendRequest(reqJson, req)) {
        std::cerr << "error: failed to parse backend request JSON\n";
        return 1;
    }

    bool verbose = req.verbose;

    // Per-value validation of backendExtras inputs. Centralised unknown-key
    // rejection (deserializeBackendRequest) is silent-tolerant for Python
    // today, but every known key still has a fixed JSON type.
    if (!expectStringIfPresent(req.backendExtras, "pythonPath")) return 1;
    if (!expectStringIfPresent(req.backendExtras, "venvPath")) return 1;
    if (!expectIntegerIfPresent(req.backendExtras, "topoCheckJobs")) return 1;

    // --- Step 2: Extract backend extras ---
    // The default ``"python3"`` literal is a Windows portability hazard
    // (Windows ships ``python.exe`` / ``py.exe``, not ``python3``). When the
    // project's Topo.toml does not pin ``backendExtras.pythonPath``, fall
    // back to the cross-platform helper so users on Windows do not see a
    // generic "exit code 1" later.
    std::string pythonPath = req.backendExtras.value("pythonPath", std::string{});
    if (pythonPath.empty()) {
        auto interp = topo::platform::findPythonInterpreter();
        if (!interp.empty()) {
            pythonPath = interp[0];
            for (size_t i = 1; i < interp.size(); ++i) {
                pythonPath += ' ';
                pythonPath += interp[i];
            }
        } else {
            // No interpreter found — record the literal as a diagnostic
            // breadcrumb. Currently pythonPath is only used for verbose
            // logging; if a future change shells out to it, the helper
            // will be the authoritative source.
            pythonPath = "python3";
        }
    }
    std::string venvPath = req.backendExtras.value("venvPath", std::string());

    if (verbose) {
        std::cerr << "[topo-build-python] Python: " << pythonPath << "\n";
        if (!venvPath.empty()) std::cerr << "[topo-build-python] venv: " << venvPath << "\n";
    }

    // --- Step 3: Collect .py source files ---
    std::vector<std::string> sourceFiles;
    for (const auto& src : req.sources) {
        fs::path srcPath(src);
        std::error_code ec;
        const bool isDir = fs::is_directory(srcPath, ec);
        if (ec) {
            std::cerr << "warning: cannot stat " << srcPath << ": " << ec.message() << "\n";
            continue;
        }
        if (isDir) {
            // Non-throwing iteration (error_code construction + increment,
            // same pattern as PythonAnalysisProvider): a permission denial
            // or a vanishing entry mid-scan must degrade to a diagnostic,
            // never abort the process.
            fs::recursive_directory_iterator it(
                srcPath, fs::directory_options::skip_permission_denied, ec);
            if (ec) {
                std::cerr << "warning: cannot scan " << srcPath << ": " << ec.message() << "\n";
                continue;
            }
            for (; it != fs::recursive_directory_iterator(); it.increment(ec)) {
                if (ec) {
                    std::cerr << "warning: directory scan of " << srcPath
                              << " stopped: " << ec.message() << "\n";
                    break;
                }
                if (it->path().extension() == ".py")
                    sourceFiles.push_back(it->path().string());
            }
        } else if (srcPath.extension() == ".py") {
            sourceFiles.push_back(srcPath.string());
        }
    }
    std::sort(sourceFiles.begin(), sourceFiles.end());

    if (verbose) {
        std::cerr << "[topo-build-python] Found " << sourceFiles.size() << " Python source file(s)\n";
        for (const auto& f : sourceFiles)
            std::cerr << "  " << f << "\n";
    }

    // --- Step 4: Run completeness check ---
    if (!req.config.noVerify) {
        std::cerr << "[1/1] Checking Python sources against declarations...\n";

        // Per-file extraction is independent; on large Python sources
        // a serial loop is the wall-clock bottleneck of the check
        // step. Honour `[check].jobs` (the same setting topo-check
        // uses) via the `topoCheckJobs` backendExtras key or the
        // `TOPO_CHECK_JOBS` env var; default to hardware concurrency
        // capped at the file count. `0` (or absent) means auto;
        // explicit `1` keeps the historical serial behaviour for
        // any caller that needs deterministic ordering.
        int jobs = 0;
        if (req.backendExtras.contains("topoCheckJobs") &&
            req.backendExtras["topoCheckJobs"].is_number_integer()) {
            jobs = req.backendExtras["topoCheckJobs"].get<int>();
        }
        if (jobs <= 0) {
            if (const char* env = std::getenv("TOPO_CHECK_JOBS")) {
                try { jobs = std::max(0, std::stoi(env)); }
                catch (...) { jobs = 0; }
            }
        }
        if (jobs <= 0) {
            unsigned hw = std::thread::hardware_concurrency();
            jobs = static_cast<int>(hw == 0 ? 1u : hw);
        }
        if (static_cast<size_t>(jobs) > sourceFiles.size())
            jobs = static_cast<int>(std::max<size_t>(sourceFiles.size(), 1));

        std::vector<topo::check::HostSymbol> hostSymbols;
        if (jobs <= 1 || sourceFiles.size() <= 1) {
            topo::check::PythonSymbolExtractor extractor;
            hostSymbols = extractor.extractAll(sourceFiles);
        } else {
            // Worker pool: each worker holds its own extractor instance
            // (the extractor carries small per-file state buffers; one
            // instance per thread avoids data races without needing
            // mutex protection inside the extractor).
            std::vector<std::vector<topo::check::HostSymbol>> shards(jobs);
            std::vector<std::future<void>> futures;
            futures.reserve(jobs);
            for (int t = 0; t < jobs; ++t) {
                futures.push_back(std::async(std::launch::async, [&, t]() {
                    topo::check::PythonSymbolExtractor extractor;
                    auto& shard = shards[t];
                    for (size_t i = t; i < sourceFiles.size();
                         i += static_cast<size_t>(jobs)) {
                        auto syms = extractor.extractSymbols(sourceFiles[i]);
                        shard.insert(shard.end(),
                                     std::make_move_iterator(syms.begin()),
                                     std::make_move_iterator(syms.end()));
                    }
                }));
            }
            for (auto& f : futures) f.get();
            for (auto& shard : shards) {
                hostSymbols.insert(hostSymbols.end(),
                                   std::make_move_iterator(shard.begin()),
                                   std::make_move_iterator(shard.end()));
            }
        }

        if (verbose) {
            std::cerr << "[topo-build-python] Extracted " << hostSymbols.size()
                      << " host symbol(s) (jobs=" << jobs << ")\n";
        }

        topo::check::CompletenessConfig compCfg;
        // Python has no destructors; constructors are __init__ -- skip by default
        compCfg.ignoreConstructors = true;
        compCfg.ignoreMain = true;

        topo::check::CheckResult result;
        topo::check::checkCompleteness(hostSymbols, req.symbolTable, req.visibilityEntries, compCfg, result);

        // --- Step 5: Report diagnostics ---
        for (const auto& diag : result.diagnostics) {
            const char* level = "note";
            if (diag.severity == topo::check::Severity::Error)
                level = "error";
            else if (diag.severity == topo::check::Severity::Warning)
                level = "warning";

            std::cerr << level << ": " << diag.message << "\n";
            if (!diag.file.empty()) {
                std::cerr << "  --> " << diag.file;
                if (diag.line > 0) std::cerr << ":" << diag.line;
                std::cerr << "\n";
            }
        }

        if (result.truncated) std::cerr << "warning: diagnostics truncated\n";

        bool hasError = result.errorCount > 0 && !req.config.warnOnly;
        if (hasError) {
            std::cerr << "[topo-build-python] Check failed (" << result.errorCount << " error(s), "
                      << result.warningCount << " warning(s)).\n";
            return 1;
        }

        if (result.diagnostics.empty()) {
            std::cerr << "[topo-build-python] All checks passed.\n";
        } else {
            std::cerr << "[topo-build-python] Checks completed (" << result.warningCount << " warning(s)).\n";
        }
    } else if (verbose) {
        std::cerr << "[topo-build-python] Skipping verification (noVerify).\n";
    }

    // No compilation or bytecode transforms for Python.
    // Create output directory for consistency with other backends.
    if (!req.config.outputPath.empty()) {
        std::error_code ec;
        fs::create_directories(req.config.outputPath, ec);
    }

    return 0;
}
