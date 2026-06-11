// Functional E2E for the python backend through the topo-build CLI funnel:
// exit-code propagation, integrated-check wiring (checks run on EVERY build
// by default), --no-verify / --no-check interplay, and diagnostic surfacing.
//
// Self-contained on purpose: the generic cross-backend harness lives in the
// backend repos as local forks; this suite only needs the topo-build exe,
// the fixtures dir, and a PATH prepend for the spawned tool chain
// (topo-build dispatches to topo-build-python and spawns topo-check).

#include "topo/Platform/Process.h"

#include <gtest/gtest.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace topo::test::e2e {
namespace {

// One-time, process-global PATH prepend (setenv/_putenv_s, the same pattern
// the backend e2e harnesses use): the whole subprocess chain spawned by
// topo-build must see topo-build-python and topo-check.
void prependToolDirsOnce() {
    static bool done = false;
    if (done) return;
    done = true;
#ifdef _WIN32
    const char pathSep = ';';
#else
    const char pathSep = ':';
#endif
    std::string dirs;
#ifdef TOPO_FUNC_E2E_TOOL_DIRS
    dirs = TOPO_FUNC_E2E_TOOL_DIRS;
#endif
    if (dirs.empty()) return;
    // The define is ';'-joined by CMake; rejoin with the platform PATH
    // separator (identical on Windows, ':' on POSIX).
    std::string prefix;
    std::string::size_type start = 0;
    while (start <= dirs.size()) {
        auto end = dirs.find(';', start);
        if (end == std::string::npos) end = dirs.size();
        if (end > start) {
            if (!prefix.empty()) prefix += pathSep;
            prefix.append(dirs, start, end - start);
        }
        if (end == dirs.size()) break;
        start = end + 1;
    }
    const char* oldPath = std::getenv("PATH");
    std::string newPath = prefix;
    if (oldPath && *oldPath) {
        newPath += pathSep;
        newPath += oldPath;
    }
#ifdef _WIN32
    _putenv_s("PATH", newPath.c_str());
#else
    setenv("PATH", newPath.c_str(), 1);
#endif
}

} // namespace

class PythonFunctional : public ::testing::Test {
protected:
    fs::path topoBuildExe_;
    fs::path pythonFixturesDir_;

    void SetUp() override {
#ifdef TOPO_BUILD_EXE
        topoBuildExe_ = fs::path(TOPO_BUILD_EXE);
#endif
        ASSERT_FALSE(topoBuildExe_.empty()) << "TOPO_BUILD_EXE not set";
        ASSERT_TRUE(fs::exists(topoBuildExe_)) << "topo-build not found: " << topoBuildExe_;
#ifdef TOPO_PYTHON_FIXTURES_DIR
        pythonFixturesDir_ = fs::path(TOPO_PYTHON_FIXTURES_DIR);
#endif
        ASSERT_FALSE(pythonFixturesDir_.empty()) << "TOPO_PYTHON_FIXTURES_DIR not set";
        ASSERT_TRUE(fs::exists(pythonFixturesDir_)) << "Python fixtures dir not found: " << pythonFixturesDir_;
        prependToolDirsOnce();
    }

    struct FullResult {
        int exitCode = -1;
        std::string stdoutOutput;
        std::string stderrOutput;
    };

    FullResult runTopoBuild(const fs::path& projDir,
                            const std::vector<std::string>& extraArgs = {}) {
        // Fresh-run guarantee: a warm check cache replaces diagnostics with
        // "Result: FAIL (cached)", defeating the substring assertions below.
        // .topo-check-cache is a project-root FILE, NOT inside .topo-cache/ —
        // both must go. The build/ output goes too so fixture trees stay
        // clean in a repo checkout across runs.
        std::error_code ec;
        fs::remove_all(projDir / ".topo-cache", ec);
        fs::remove_all(projDir / ".topo-check-cache", ec);
        fs::remove_all(projDir / "build", ec);
        auto r = platform::runProcessCapture(topoBuildExe_.generic_string(),
                                             extraArgs, projDir.generic_string());
        return FullResult{r.exitCode, r.stdoutOutput, r.stderrOutput};
    }

    FullResult topoBuildPython(const std::string& projectName,
                               const std::vector<std::string>& extraArgs = {}) {
        return runTopoBuild(pythonFixturesDir_ / projectName, extraArgs);
    }
};

// --- Completeness Pass ---
TEST_F(PythonFunctional, CompletenessPass) {
    auto r = topoBuildPython("completeness_pass");
    ASSERT_EQ(r.exitCode, 0) << "topo-build failed:\n" << r.stderrOutput;
    EXPECT_NE(r.stderrOutput.find("All checks passed"), std::string::npos)
        << "Expected 'All checks passed' in stderr:\n" << r.stderrOutput;
}

// --- Completeness Violation ---
TEST_F(PythonFunctional, CompletenessViolation) {
    auto r = topoBuildPython("completeness_violation");
    EXPECT_NE(r.exitCode, 0) << "Build should fail for undeclared symbols:\n" << r.stderrOutput;
    // Check diagnostics land on STDOUT on a fresh run; stderr carries the
    // stable topo-build funnel line.
    EXPECT_NE(r.stdoutOutput.find("is not declared in .topo"), std::string::npos)
        << "Expected completeness diagnostic on stdout:\n" << r.stdoutOutput;
    EXPECT_NE(r.stderrOutput.find("topo-check failed"), std::string::npos)
        << "Expected 'topo-check failed' in stderr:\n" << r.stderrOutput;
}

// --- Dangling Declaration ---
TEST_F(PythonFunctional, DanglingDeclaration) {
    auto r = topoBuildPython("dangling_declaration");
    EXPECT_EQ(r.exitCode, 0) << "Dangling declarations are warnings, not errors:\n" << r.stderrOutput;
    EXPECT_NE(r.stderrOutput.find("warning"), std::string::npos)
        << "Expected warning for dangling declaration:\n" << r.stderrOutput;
}

// --- Visibility Mismatch ---
TEST_F(PythonFunctional, VisibilityMismatch) {
    auto r = topoBuildPython("visibility_mismatch");
    EXPECT_NE(r.exitCode, 0) << "Build should fail for visibility mismatch:\n" << r.stderrOutput;
    EXPECT_NE(r.stdoutOutput.find("declared public in .topo but private in host code"),
              std::string::npos)
        << "Expected visibility diagnostic on stdout:\n" << r.stdoutOutput;
    EXPECT_NE(r.stderrOutput.find("topo-check failed"), std::string::npos)
        << "Expected 'topo-check failed' in stderr:\n" << r.stderrOutput;
}

// --- Nested Classes ---
TEST_F(PythonFunctional, NestedClasses) {
    auto r = topoBuildPython("nested_classes");
    ASSERT_EQ(r.exitCode, 0) << "topo-build failed for nested classes:\n" << r.stderrOutput;
    EXPECT_NE(r.stderrOutput.find("All checks passed"), std::string::npos)
        << "Expected 'All checks passed' in stderr:\n" << r.stderrOutput;
}

// --- No Verify ---
TEST_F(PythonFunctional, NoVerify) {
    // The fixture deliberately mismatches its .topo. --no-verify only skips
    // the backend's own verification; checks run on EVERY build by default,
    // so --no-check is required as well for the build to succeed.
    auto r = topoBuildPython("no_verify", {"--no-verify", "--no-check"});
    ASSERT_EQ(r.exitCode, 0) << "Build should succeed with --no-verify --no-check:\n" << r.stderrOutput;
    // Should not contain check-related output
    EXPECT_EQ(r.stderrOutput.find("Checking Python sources"), std::string::npos)
        << "Should skip checks with --no-verify --no-check:\n" << r.stderrOutput;
}

// --- Build Error: Missing Sources ---
TEST_F(PythonFunctional, BuildErrorMissingSources) {
    fs::path tempDir = fs::temp_directory_path() / "topo-e2e-python-missing";
    fs::create_directories(tempDir / "topo");

    // Topo.toml pointing to nonexistent source directory
    {
        std::ofstream f(tempDir / "Topo.toml");
        f << "[project]\nname = \"bad_python\"\n\n"
          << "[topo]\nroot = \"topo/main.topo\"\n\n"
          << "[build]\nlanguage = \"python\"\n"
          << "sources = [\"nonexistent_dir\"]\n"
          << "output = \"build/out\"\n\n"
          << "[builder]\nmode = \"dev\"\n";
    }

    // Minimal .topo file
    {
        std::ofstream f(tempDir / "topo" / "main.topo");
        f << "using int = std::python::int;\n\n"
          << "namespace app {\n  public:\n    int something();\n}\n";
    }

    auto r = runTopoBuild(tempDir);

    // Should fail or at least report no sources found
    // (exact behavior depends on whether empty source list is an error)
    if (r.exitCode == 0) {
        // If it succeeds, it should warn about dangling declarations
        EXPECT_NE(r.stderrOutput.find("warning"), std::string::npos)
            << "Should warn about missing symbols when sources dir doesn't exist:\n" << r.stderrOutput;
    }

    std::error_code ec;
    fs::remove_all(tempDir, ec);
}

} // namespace topo::test::e2e
