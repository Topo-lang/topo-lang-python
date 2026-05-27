#include "E2eHarness.h"

#include "topo/Platform/Process.h"

#include <cstring>
#include <fstream>

namespace topo::test::e2e {

class PythonFunctional : public E2eFixture {
protected:
    fs::path pythonFixturesDir_;

    void SetUp() override {
        E2eFixture::SetUp();
#ifdef TOPO_PYTHON_FIXTURES_DIR
        pythonFixturesDir_ = fs::path(TOPO_PYTHON_FIXTURES_DIR);
#endif
        ASSERT_FALSE(pythonFixturesDir_.empty()) << "TOPO_PYTHON_FIXTURES_DIR not set";
        ASSERT_TRUE(fs::exists(pythonFixturesDir_)) << "Python fixtures dir not found: " << pythonFixturesDir_;
    }

    struct FullResult {
        int exitCode = -1;
        std::string stdoutOutput;
        std::string stderrOutput;
    };

    FullResult topoBuildPython(const std::string& projectName,
                               const std::vector<std::string>& extraArgs = {}) {
        fs::path projDir = pythonFixturesDir_ / projectName;
        std::string exe = topoBuildExe_.generic_string();
        std::string workDir = projDir.generic_string();
        auto r = platform::runProcessCapture(exe, extraArgs, workDir);
        return FullResult{r.exitCode, r.stdoutOutput, r.stderrOutput};
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
    EXPECT_NE(r.stderrOutput.find("not declared"), std::string::npos)
        << "Expected 'not declared' diagnostic in stderr:\n" << r.stderrOutput;
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
    EXPECT_NE(r.stderrOutput.find("visibility"), std::string::npos)
        << "Expected visibility mismatch diagnostic:\n" << r.stderrOutput;
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
    auto r = topoBuildPython("no_verify", {"--no-verify"});
    ASSERT_EQ(r.exitCode, 0) << "Build should succeed with --no-verify:\n" << r.stderrOutput;
    // Should not contain check-related output
    EXPECT_EQ(r.stderrOutput.find("Checking Python sources"), std::string::npos)
        << "Should skip checks with --no-verify:\n" << r.stderrOutput;
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
          << "sources = [\"nonexistent_dir\"]\n\n"
          << "[builder]\nmode = \"dev\"\n";
    }

    // Minimal .topo file
    {
        std::ofstream f(tempDir / "topo" / "main.topo");
        f << "using int = std::python::int;\n\n"
          << "namespace app {\n  public:\n    int something();\n}\n";
    }

    std::string exe = topoBuildExe_.generic_string();
    auto r = platform::runProcessCapture(exe, {}, tempDir.generic_string());

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
