// Unit tests for PythonImportExtractor (L1 regex import extraction).
//
// Regression coverage for two scanner desync bugs:
//   - a `#` comment containing `"""` falsely entering multi-line-string
//     mode and swallowing every later import (triple-quote count must run
//     AFTER the comment-only-line skip);
//   - indented `import X` parsed with a hardcoded "+7" offset that ignores
//     leading whitespace and yields garbage module names.

#include "analysis/extract/PythonImportExtractor.h"
#include <gtest/gtest.h>
#include <algorithm>
#include <filesystem>
#include <fstream>

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

namespace fs = std::filesystem;
using namespace topo::check;

class PythonImportExtractorTest : public ::testing::Test {
protected:
    void SetUp() override {
        tempDir_ = fs::temp_directory_path() /
                   ("topo_py_import_test_" + std::to_string(topo_getpid()));
        fs::create_directories(tempDir_);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(tempDir_, ec);
    }

    std::vector<HostImport> extract(const std::string& source) {
        auto path = tempDir_ / "mod.py";
        // Binary mode: the extractor reads raw bytes; keep CR/LF exactly.
        std::ofstream ofs(path, std::ios::binary);
        ofs << source;
        ofs.close();
        PythonImportExtractor ex;
        return ex.extractImports(path.string());
    }

    static bool hasModule(const std::vector<HostImport>& imports,
                          const std::string& mod) {
        return std::any_of(imports.begin(), imports.end(),
                           [&](const HostImport& i) { return i.normalizedPath == mod; });
    }

    fs::path tempDir_;
};

// A `#` comment containing a stray `"""` must NOT enter multi-line-string
// mode; the import that follows must still be captured.
TEST_F(PythonImportExtractorTest, CommentWithTripleQuoteDoesNotSwallowImports) {
    auto imports = extract(
        "# this comment mentions \"\"\" triple quotes\n"
        "import os\n"
        "import sys\n");
    EXPECT_TRUE(hasModule(imports, "os"));
    EXPECT_TRUE(hasModule(imports, "sys"));
}

// Indented `import X` (inside a function/conditional) must resolve to the
// real module name, not a garbage prefix from a fixed offset.
TEST_F(PythonImportExtractorTest, IndentedImportResolvesModuleName) {
    auto imports = extract(
        "def f():\n"
        "    import os\n"
        "    import socket\n");
    EXPECT_TRUE(hasModule(imports, "os"));
    EXPECT_TRUE(hasModule(imports, "socket"));
    // No truncated/garbage module names.
    EXPECT_FALSE(hasModule(imports, "s"));
    EXPECT_FALSE(hasModule(imports, "t"));
}

// Indented dotted + aliased import keeps top-level module classification.
TEST_F(PythonImportExtractorTest, IndentedDottedImportTopLevel) {
    auto imports = extract(
        "if cond:\n"
        "        import os.path as p\n");
    EXPECT_TRUE(hasModule(imports, "os"));
}

// A genuine docstring still suppresses imports inside it (no over-correction).
TEST_F(PythonImportExtractorTest, DocstringStillSuppressesImports) {
    auto imports = extract(
        "\"\"\"\n"
        "import should_not_count\n"
        "\"\"\"\n"
        "import real_module\n");
    EXPECT_TRUE(hasModule(imports, "real_module"));
    EXPECT_FALSE(hasModule(imports, "should_not_count"));
}
