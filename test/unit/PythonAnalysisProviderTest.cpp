// Unit tests for PythonAnalysisProvider::collectSourceFiles — source
// discovery over the project dir + src/ search roots.
//
// Regression focus: a regular FILE named "src" at the project root used to
// be fed to fs::recursive_directory_iterator, whose "Not a directory"
// filesystem_error aborted the whole checker (rc=134). File search roots
// must be handled as single sources and directory iteration must degrade,
// not throw.

#include "analysis/PythonAnalysisProvider.h"

#include <gtest/gtest.h>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using namespace topo::check;

#ifdef _WIN32
#include <process.h>
static int topo_getpid() {
    return _getpid();
}
#else
#include <unistd.h>
static int topo_getpid() {
    return getpid();
}
#endif

class PythonAnalysisProviderTest : public ::testing::Test {
protected:
    void SetUp() override {
        projectDir_ = fs::temp_directory_path() /
            ("topo_python_provider_test_" + std::to_string(topo_getpid()));
        fs::create_directories(projectDir_);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(projectDir_, ec);
    }

    std::string writeFile(const fs::path& rel, const std::string& content) {
        auto path = projectDir_ / rel;
        std::ofstream ofs(path);
        ofs << content;
        return path.string();
    }

    static int countOf(const std::vector<std::string>& files, const std::string& path) {
        return static_cast<int>(std::count(files.begin(), files.end(), path));
    }

    fs::path projectDir_;
};

TEST_F(PythonAnalysisProviderTest, SrcEntryThatIsRegularFileDoesNotAbort) {
    auto mainPy = writeFile("main.py", "def main():\n    return 0\n");
    // A regular FILE shadowing the src/ search root: iterating it threw
    // "Not a directory" and aborted before the is_regular_file guard.
    writeFile("src", "not a directory\n");

    auto provider = createPythonAnalysisProvider();
    auto files = provider->collectSourceFiles(projectDir_.string(), {});

    EXPECT_EQ(countOf(files, mainPy), 1);
}

TEST_F(PythonAnalysisProviderTest, MissingSrcDirDegradesToSkip) {
    auto mainPy = writeFile("main.py", "def main():\n    return 0\n");

    auto provider = createPythonAnalysisProvider();
    auto files = provider->collectSourceFiles(projectDir_.string(), {});

    EXPECT_EQ(countOf(files, mainPy), 1);
}
