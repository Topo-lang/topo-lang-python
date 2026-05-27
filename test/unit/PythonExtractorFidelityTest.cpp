// PythonExtractorFidelityTest -- golden-fidelity tests for
// topo_extract_python.py.
//
// Each fixture is a subdirectory under PYTHON_FIDELITY_FIXTURES_DIR:
//
//     <fixture>/
//         input.py        — Python source fed to the extractor
//         request.json    — human-readable description (not consumed by
//                           the extractor; present for parity with the
//                           other language fidelity suites)
//         expected.json   — golden {"callSites": [...]} output
//
// Test body: spawn `python3 <script> <abs/input.py>`, parse stdout as
// JSON, canonicalise the "file" fields to "input.py" (so fixtures remain
// portable across checkout paths), and compare to expected.json using
// nlohmann::json equality.
//
// If expected.json or input.py is missing the test is reported as a
// GTEST_SKIP so a malformed fixture is visible but does not wedge CI.
// The driver also skips when python3 cannot be found on PATH — that is
// the only environment-level skip reason and it is surfaced in the test
// output.

#include "topo/Platform/Process.h"

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using json = nlohmann::json;

#ifndef PYTHON_FIDELITY_FIXTURES_DIR
#error "PYTHON_FIDELITY_FIXTURES_DIR must be defined by CMake"
#endif

#ifndef PYTHON_EXTRACTOR_SCRIPT_PATH
#error "PYTHON_EXTRACTOR_SCRIPT_PATH must be defined by CMake"
#endif

namespace {

std::string readFile(const fs::path& path) {
    std::ifstream ifs(path);
    std::ostringstream ss;
    ss << ifs.rdbuf();
    return ss.str();
}

/// Replace every "file" value inside a {"callSites": [...]} payload with
/// just the basename, so fixtures are portable across checkout paths.
void canonicaliseFilePaths(json& payload) {
    if (!payload.is_object() || !payload.contains("callSites")) return;
    auto& arr = payload["callSites"];
    if (!arr.is_array()) return;
    for (auto& entry : arr) {
        if (!entry.is_object() || !entry.contains("file")) continue;
        if (!entry["file"].is_string()) continue;
        fs::path p = entry["file"].get<std::string>();
        entry["file"] = p.filename().string();
    }
}

struct FixtureRun {
    bool ok = false;
    std::string failureReason;
    json actual;
    json expected;
};

FixtureRun runFixture(const fs::path& fixtureDir) {
    FixtureRun run;

    fs::path inputPath = fixtureDir / "input.py";
    fs::path expectedPath = fixtureDir / "expected.json";

    if (!fs::exists(inputPath)) {
        run.failureReason = "missing input.py in " + fixtureDir.string();
        return run;
    }
    if (!fs::exists(expectedPath)) {
        run.failureReason = "missing expected.json in " + fixtureDir.string();
        return run;
    }

    try {
        run.expected = json::parse(readFile(expectedPath));
    } catch (const json::exception& e) {
        run.failureReason =
            std::string("expected.json is not valid JSON: ") + e.what();
        return run;
    }

    // Run: python3 <script> <input.py>
    std::vector<std::string> args = {
        std::string(PYTHON_EXTRACTOR_SCRIPT_PATH),
        inputPath.string(),
    };

    auto result = topo::platform::runProcessCaptureWithTimeout(
        "python3", args, /*timeoutMs=*/5000);

    if (result.exitCode != 0) {
        run.failureReason = "python3 exited with " +
                            std::to_string(result.exitCode) +
                            "; stderr=" + result.stderrOutput;
        return run;
    }

    try {
        run.actual = json::parse(result.stdoutOutput);
    } catch (const json::exception& e) {
        run.failureReason = std::string("extractor emitted non-JSON: ") +
                            e.what() +
                            "; stdout=" + result.stdoutOutput;
        return run;
    }

    canonicaliseFilePaths(run.actual);
    canonicaliseFilePaths(run.expected);

    run.ok = true;
    return run;
}

/// Diagnostic helper — renders the diff when a fidelity assertion fires
/// so the failure message pinpoints which callSites mismatched.
std::string dumpDiff(const json& expected, const json& actual) {
    std::ostringstream ss;
    ss << "\nexpected: " << expected.dump(2);
    ss << "\nactual:   " << actual.dump(2);
    return ss.str();
}

} // namespace

// Each TEST is expanded at file scope — GTest generates free functions so
// they need TU-level linkage, not internal linkage inside an anonymous
// namespace.  The helpers in the anonymous namespace above are TU-private
// but reachable here because the TU is a single translation unit.
#define FIDELITY_TEST(Name, FixtureName)                                       \
    TEST(PythonExtractorFidelity, Name) {                                      \
        fs::path fixtureDir =                                                  \
            fs::path(PYTHON_FIDELITY_FIXTURES_DIR) / FixtureName;              \
        auto run = runFixture(fixtureDir);                                     \
        if (!run.ok) {                                                         \
            GTEST_SKIP() << run.failureReason;                                 \
        } else {                                                               \
            EXPECT_EQ(run.expected, run.actual) << dumpDiff(run.expected,      \
                                                            run.actual);      \
        }                                                                      \
    }

// Fixtures are named with a leading ordinal so `ls` and ctest output
// preserve the intended reading order.
FIDELITY_TEST(BasicFunction, "01_basic_function")
FIDELITY_TEST(ClassMethod, "02_class_method")
FIDELITY_TEST(Decorator, "03_decorator")
FIDELITY_TEST(ClassInheritance, "04_class_inheritance")
FIDELITY_TEST(MangledName, "05_mangled_name")
FIDELITY_TEST(AsyncDef, "06_async_def")
FIDELITY_TEST(ModuleScope, "07_module_scope")
FIDELITY_TEST(Lambda, "08_lambda")
FIDELITY_TEST(Comprehension, "09_comprehension")
FIDELITY_TEST(TypeHints, "10_type_hints")
FIDELITY_TEST(FString, "11_f_string")
FIDELITY_TEST(NestedFunction, "12_nested_function")
FIDELITY_TEST(WildcardImport, "13_wildcard_import")
