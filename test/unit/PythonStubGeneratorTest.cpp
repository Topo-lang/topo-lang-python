// Unit tests for PythonStubGenerator.

#include "analysis/stub/PythonStubGenerator.h"
#include <gtest/gtest.h>
#include <filesystem>
#include <fstream>
#include <sstream>

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

// ---------------------------------------------------------------------------
// Test fixture
// ---------------------------------------------------------------------------

class PythonStubGeneratorTest : public ::testing::Test {
protected:
    void SetUp() override {
        tempDir_ = fs::temp_directory_path() / ("topo_py_stub_test_" + std::to_string(topo_getpid()));
        fs::create_directories(tempDir_);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(tempDir_, ec);
    }

    std::string writeTempFile(const std::string& name, const std::string& content) {
        auto path = tempDir_ / name;
        // Binary mode: PythonStubGenerator reads/writes the file as raw
        // bytes, so the test fixture must too — otherwise Windows text
        // mode injects \r before every \n on write and the generator's
        // \n-based parse misses the function bounds.
        std::ofstream ofs(path, std::ios::binary);
        ofs << content;
        return path.string();
    }

    std::string readFileContent(const std::string& path) {
        std::ifstream ifs(path, std::ios::binary);
        std::ostringstream ss;
        ss << ifs.rdbuf();
        return ss.str();
    }

    /// Write source to a temp file, stub the named function, return the
    /// modified file content.  Returns "STUB_FAILED: <error>" on failure.
    std::string stubAndRead(const std::string& source, const std::string& funcName) {
        auto path = writeTempFile("test_module.py", source);

        PythonStubGenerator gen;
        auto result = gen.stubFunction(path, funcName);
        if (!result.success) return "STUB_FAILED: " + result.error;

        auto content = readFileContent(path);
        gen.restoreFile(path, result);
        return content;
    }

    fs::path tempDir_;
};

// 1. NoReturnAnnotation — body replaced with `pass`, original body gone.
TEST_F(PythonStubGeneratorTest, NoReturnAnnotation) {
    auto stubbed = stubAndRead(
        "def greet(name):\n"
        "    print(f'Hello {name}')\n"
        "    print('!')\n",
        "greet");
    EXPECT_NE(stubbed.find("pass"), std::string::npos);
    EXPECT_EQ(stubbed.find("print"), std::string::npos);
}

// 2. NoneReturn — -> None body replaced with `pass`.
TEST_F(PythonStubGeneratorTest, NoneReturn) {
    auto stubbed = stubAndRead(
        "def cleanup() -> None:\n"
        "    os.remove('tmp')\n",
        "cleanup");
    EXPECT_NE(stubbed.find("pass"), std::string::npos);
}

// 3. BoolReturn — -> bool stub returns False.
TEST_F(PythonStubGeneratorTest, BoolReturn) {
    auto stubbed = stubAndRead(
        "def is_valid(x: int) -> bool:\n"
        "    return x > 0\n",
        "is_valid");
    EXPECT_NE(stubbed.find("return False"), std::string::npos);
}

// 4. IntReturn — -> int stub returns 0.
TEST_F(PythonStubGeneratorTest, IntReturn) {
    auto stubbed = stubAndRead(
        "def count() -> int:\n"
        "    return len(items)\n",
        "count");
    EXPECT_NE(stubbed.find("return 0"), std::string::npos);
}

// 5. ObjectReturn — -> Engine stub returns None.
TEST_F(PythonStubGeneratorTest, ObjectReturn) {
    auto stubbed = stubAndRead(
        "def get_engine() -> Engine:\n"
        "    return Engine()\n",
        "get_engine");
    EXPECT_NE(stubbed.find("return None"), std::string::npos);
}

// 6. IndentedBody — method inside a class: body replaced, class retained.
TEST_F(PythonStubGeneratorTest, IndentedBody) {
    auto stubbed = stubAndRead(
        "class Svc:\n"
        "    def run(self) -> bool:\n"
        "        x = 1\n"
        "        return x > 0\n",
        "run");
    EXPECT_NE(stubbed.find("return False"), std::string::npos);
    // Original body should be gone
    EXPECT_EQ(stubbed.find("x = 1"), std::string::npos);
    // Class declaration should remain
    EXPECT_NE(stubbed.find("class Svc:"), std::string::npos);
}

// 7. MultiLineSignature — parenthesized signature spanning multiple lines.
TEST_F(PythonStubGeneratorTest, MultiLineSignature) {
    auto stubbed = stubAndRead(
        "def complex(\n"
        "    x: int,\n"
        "    y: int\n"
        ") -> int:\n"
        "    return x + y\n",
        "complex");
    EXPECT_NE(stubbed.find("return 0"), std::string::npos);
    EXPECT_EQ(stubbed.find("x + y"), std::string::npos);
}

// 8. DecoratorPreserved — @staticmethod decorator is kept in stubbed output.
TEST_F(PythonStubGeneratorTest, DecoratorPreserved) {
    auto stubbed = stubAndRead(
        "@staticmethod\n"
        "def create() -> 'Svc':\n"
        "    return Svc()\n",
        "create");
    EXPECT_NE(stubbed.find("@staticmethod"), std::string::npos);
    EXPECT_NE(stubbed.find("return None"), std::string::npos);
    EXPECT_EQ(stubbed.find("Svc()"), std::string::npos);
}
