// E2E tests for Python containment checks.
// Each test constructs a CheckConfig pointing at a fixture project,
// then verifies the exit code (0 = pass, 1 = errors found).

#include "CheckRunner.h"

#include <gtest/gtest.h>
#include <cstdlib>

using namespace topo;

static std::string fixtureDir(const char* name) {
    return std::string(TOPO_TEST_FIXTURES_DIR) + "/" + name;
}

// --- Existing Python containment tests ---

TEST(PythonContainment, Fail) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_fail");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, ExternalOk) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_external_ok");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 0);
}

TEST(PythonContainment, ExternalClassMethodOk) {
    // A class method declared `external` in .topo (host emits callerQN
    // `Renderer.render`) must be recognised as external via the simple-name
    // fallback. Requires LanguageAnalysisProvider::separator() == "." for
    // Python so ContainmentCheck splits the qualifiedName on the
    // language-native separator rather than a hardcoded "::".
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_external_class_method_ok");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 0);
}

// --- Escape mechanisms ---

TEST(PythonContainment, Eval) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_eval");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, Ctypes) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_ctypes");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

// --- System calls ---

TEST(PythonContainment, Subprocess) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_subprocess");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, Importlib) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_importlib");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, OsFile) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_os_file");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, Network) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_network_python");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, Thread) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_thread_python");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

// --- Safe code (should pass) ---

TEST(PythonContainment, SafeCode) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_safe_code_python");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 0);
}

// --- Adversarial containment fixtures ---
// 9 fixtures covering dangerous-safelist, catalog gaps, and wildcard
// import paths. Mode = "force" so violations become Errors.

TEST(PythonContainment, GetattrDynamic) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_getattr_dynamic");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, GetattrStatic) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_getattr_static");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, MarshalLoad) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_marshal_load");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, YamlLoadUnsafe) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_yaml_load_unsafe");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, ClassPunning) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_class_punning");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, Settrace) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_settrace");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, WildcardImportOs) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_wildcard_import_os");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, WildcardImportTyping) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_wildcard_import_typing");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 0);
}

TEST(PythonContainment, SafeLocal) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_safe_local");
    cfg.checkName = "containment";
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 0);
}

// --- L2 deep-mode coverage ---
// These tests exercise the Python ast-subprocess extractor inside
// PythonSafetyAnalyzer directly (pyright does not implement
// textDocument/semanticTokens/full, so L2 falls through the ast path).
// Without deepMode=true these fixtures would only exercise the L1 regex
// extractor, leaving the subprocess path completely untested in CI.

TEST(PythonContainment, DeepOsFile) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_os_file");
    cfg.checkName = "containment";
    cfg.deepMode = true;
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, DeepExternalOk) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_external_ok");
    cfg.checkName = "containment";
    cfg.deepMode = true;
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 0);
}

TEST(PythonContainment, DeepWildcardImportOs) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_wildcard_import_os");
    cfg.checkName = "containment";
    cfg.deepMode = true;
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}

TEST(PythonContainment, DeepClassPunning) {
    CheckConfig cfg;
    cfg.projectDir = fixtureDir("containment_python_class_punning");
    cfg.checkName = "containment";
    cfg.deepMode = true;
    CheckRunner runner(cfg);
    ASSERT_TRUE(runner.loadConfig());
    EXPECT_EQ(runner.run(), 1);
}
