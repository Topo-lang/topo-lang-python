# Checker E2E suite (completeness + containment incl. the Deep* L2 cases
# + purity + visibility + stage-isolation) — in its own file so a
# meta-style super-build can re-add it AFTER its topo-cli sibling
# configures (the inline add from ../CMakeLists.txt self-skips there: no
# topo::cli::TopoCheckRunner exists yet at lang-package configure time,
# and such a build puts no installed topo-cli on the prefix). Self-gating
# + idempotent: safe to add_subdirectory() twice — whichever pass first
# finds the runner defines the suite.
#
# MinGW note: the parent test/CMakeLists.txt's static-link options reach
# this suite only on the inline path (directory inheritance); a late
# re-add from another root lacks them, which is moot — only the
# standalone build runs on MinGW.

if(TARGET topo-lang-python-check-tests)
    return()
endif()

# TestMain.cpp registers the Python plugin before tests run, so the suite
# cannot stand without it. Defensive only — TopoPythonPlugin builds
# unconditionally in every configure of this package.
if(NOT TARGET TopoPythonPlugin)
    message(STATUS "topo-lang-python tests: checker-e2e suite (topo-lang-python-check-tests) "
                   "skipped — TopoPythonPlugin not in scope.")
    return()
endif()

# The suite links TopoCheckRunner + its CheckRunner.h, defined by topo-cli
# (topo::cli::TopoCheckRunner) — a package DOWNSTREAM of topo-lang-python
# (a hard dependency would be a circular edge). Two paths in:
#   * super-build — topo-cli's in-tree alias satisfies the gate on the
#     root's late re-add of this dir;
#   * standalone — an installed topo-cli on CMAKE_PREFIX_PATH exports the
#     runner. This is what the verify-l2 CI job uses, so python L2
#     containment actually RUNS on a public surface.
if(NOT TARGET topo::cli::TopoCheckRunner)
    find_package(topo-cli CONFIG QUIET)
endif()
if(NOT TARGET topo::cli::TopoCheckRunner)
    message(STATUS "topo-lang-python tests: checker-e2e suite (topo-lang-python-check-tests) "
                   "skipped — needs TopoCheckRunner (in-tree via a super-build, or an "
                   "installed topo-cli on CMAKE_PREFIX_PATH).")
    return()
endif()

# Sources and fixtures stay in test/e2e + test/fixtures; anchor on this
# file's parent so both the inline and the late add resolve them.
# (test/e2e also holds PythonFunctionalTests.cpp — a different suite,
# compiled by ../CMakeLists.txt's functional gate, NOT here.)
get_filename_component(_test_root "${CMAKE_CURRENT_SOURCE_DIR}" DIRECTORY)

add_executable(topo-lang-python-check-tests
    ${_test_root}/e2e/TestMain.cpp
    ${_test_root}/e2e/PythonCompletenessTest.cpp
    ${_test_root}/e2e/PythonContainmentTest.cpp
    ${_test_root}/e2e/PythonPurityTest.cpp
    ${_test_root}/e2e/PythonVisibilityTest.cpp
    ${_test_root}/e2e/PythonStageIsolationTest.cpp
)
target_link_libraries(topo-lang-python-check-tests PRIVATE
    topo::cli::TopoCheckRunner TopoPythonPlugin
    GTest::gtest
)
target_compile_definitions(topo-lang-python-check-tests PRIVATE
    TOPO_TEST_FIXTURES_DIR="${_test_root}/fixtures/check"
)
topo_set_compiler_flags(topo-lang-python-check-tests)
# The PythonContainment.Deep* cases drive PythonSafetyAnalyzer through
# PyrightBridge — pyright-langserver must be on PATH, or the runner's
# no-silent-degradation guard downgrades the verdict and the cases FAIL
# (they never skip). TIMEOUT 180: each deep case boots its own pyright
# instance and waits for indexing, which dominates the 60s default on CI
# runners.
gtest_discover_tests(topo-lang-python-check-tests DISCOVERY_TIMEOUT 30
    PROPERTIES LABELS "toolchain\;checker\;lang-python" TIMEOUT 180)
