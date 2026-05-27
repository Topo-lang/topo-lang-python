// topo-build-python per-value backendExtras validation tests.
//
// Spawns the actual topo-build-python binary with hand-crafted
// BackendRequest JSON to assert that wrong-typed backendExtras values
// are rejected before extraction runs. Mirrors the topo-jvm input-trust
// pattern: every backend tool must reject malformed backendExtras with a
// uniform diagnostic shape ("error: backendExtras.<key>: expected
// <type>, got <actual>") rather than silently coercing or crashing.

#include "topo/Platform/Process.h"

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif

namespace fs = std::filesystem;
using json = nlohmann::json;

namespace {

#ifdef _WIN32
int testPid() { return _getpid(); }
#else
int testPid() { return getpid(); }
#endif

class PythonBackendExtrasInputTrust : public ::testing::Test {
protected:
    fs::path testDir;

    void SetUp() override {
        testDir = fs::temp_directory_path() /
                  ("topo-python-extras-trust_" + std::to_string(testPid()) + "_" +
                   std::to_string(reinterpret_cast<std::uintptr_t>(this)));
        fs::create_directories(testDir);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(testDir, ec);
    }

    json makeRequest(const json& backendExtras) const {
        json j = json::object();
        j["outputPath"] = (testDir / "out").string();
        j["tempDir"] = (testDir / "tmp").string();
        j["language"] = "python";
        j["config"] = json::object();
        j["topoMetadata"] = json::object();
        j["visibilityEntries"] = json::array();
        j["backendExtras"] = backendExtras;
        return j;
    }

    topo::platform::CapturedProcessResult invoke(const json& req) const {
        fs::path reqPath = testDir / "request.json";
        std::ofstream(reqPath) << req.dump();
        return topo::platform::runProcessCapture(
            TOPO_BUILD_PYTHON_EXE, {reqPath.string()}, false);
    }
};

} // namespace

TEST_F(PythonBackendExtrasInputTrust, PythonPathMustBeString) {
    json extras = json::object();
    extras["pythonPath"] = json::array({"python3"});
    auto result = invoke(makeRequest(extras));

    EXPECT_NE(result.exitCode, 0);
    EXPECT_NE(result.stderrOutput.find("backendExtras.pythonPath"),
              std::string::npos)
        << "expected diagnostic mentioning 'backendExtras.pythonPath'; "
        << "stderr was:\n" << result.stderrOutput;
    EXPECT_NE(result.stderrOutput.find("expected string"), std::string::npos)
        << "expected 'expected string' phrase; stderr was:\n"
        << result.stderrOutput;
}

TEST_F(PythonBackendExtrasInputTrust, VenvPathMustBeString) {
    json extras = json::object();
    extras["venvPath"] = 7;
    auto result = invoke(makeRequest(extras));

    EXPECT_NE(result.exitCode, 0);
    EXPECT_NE(result.stderrOutput.find("backendExtras.venvPath"),
              std::string::npos)
        << "expected diagnostic mentioning 'backendExtras.venvPath'; "
        << "stderr was:\n" << result.stderrOutput;
}

TEST_F(PythonBackendExtrasInputTrust, TopoCheckJobsMustBeInteger) {
    json extras = json::object();
    extras["topoCheckJobs"] = "auto"; // historical typo: must be integer
    auto result = invoke(makeRequest(extras));

    EXPECT_NE(result.exitCode, 0);
    EXPECT_NE(result.stderrOutput.find("backendExtras.topoCheckJobs"),
              std::string::npos)
        << "expected diagnostic mentioning 'backendExtras.topoCheckJobs'; "
        << "stderr was:\n" << result.stderrOutput;
    EXPECT_NE(result.stderrOutput.find("expected integer"), std::string::npos)
        << "expected 'expected integer' phrase; stderr was:\n"
        << result.stderrOutput;
}
