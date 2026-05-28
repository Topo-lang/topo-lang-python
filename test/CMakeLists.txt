# MinGW on GHA: cached upstream topo-install may have been linked against
# a different libstdc++/libgcc ABI than C:\mingw64\bin\ currently provides,
# tripping STATUS_ENTRYPOINT_NOT_FOUND (0xc0000139) when gtest_discover_tests
# launches each binary. Static-link the C++ runtime for every test exe in
# this directory so they're hermetic.
if(MINGW)
    add_link_options(-static -static-libgcc -static-libstdc++)
endif()

# Lang type tests (header-only, no Python toolchain dependency at link time)
add_executable(topo-lang-python-tests
    unit/PythonStubGeneratorTest.cpp
    unit/PythonEmitterStdlibTest.cpp
    unit/PythonSafePatternsLoadTest.cpp
)
# TopoPythonAnalysis PUBLIC-propagates TopoPythonLSP → topo::core::TopoLSPBridge
# → topo::core::TopoPlatform. TopoPythonTranspile brings in PythonEmitter
# (PUBLIC-propagates topo::core::TopoTranspile) for the stdlib emitter tests.
target_link_libraries(topo-lang-python-tests PRIVATE
    TopoPythonAnalysis TopoPythonTranspile
    GTest::gtest_main
)
target_include_directories(topo-lang-python-tests PRIVATE
    ${PROJECT_SOURCE_DIR}/topo-check       # for analysis/stub/PythonStubGenerator.h
    ${PROJECT_SOURCE_DIR}/topo-transpile   # for PythonEmitter.h
)
topo_set_compiler_flags(topo-lang-python-tests)
gtest_discover_tests(topo-lang-python-tests DISCOVERY_TIMEOUT 30
    PROPERTIES LABELS "toolchain;lang-python;stdlib" TIMEOUT 60)

# ── Python interpreter probe for the script-based suites ─────
# Used by the extractor-fidelity gtest and the extractor smoke unittest.
find_package(Python3 COMPONENTS Interpreter QUIET)
if(NOT Python3_FOUND)
    message(STATUS "topo-lang-python tests: Python3 not found — "
                   "extractor-fidelity + extractor-smoke suites will be skipped.")
endif()

# Python extractor golden-fidelity tests.
# Spawns topo_extract_python.py as a subprocess and compares its stdout
# against checked-in expected.json files under fixtures/extractor_fidelity/.
if(Python3_FOUND)
    add_executable(topo-lang-python-extractor-fidelity-tests
        unit/PythonExtractorFidelityTest.cpp
    )
    target_link_libraries(topo-lang-python-extractor-fidelity-tests PRIVATE
        topo::core::TopoPlatform
        nlohmann_json::nlohmann_json
        GTest::gtest_main
    )
    target_compile_definitions(topo-lang-python-extractor-fidelity-tests PRIVATE
        PYTHON_FIDELITY_FIXTURES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/extractor_fidelity"
        PYTHON_EXTRACTOR_SCRIPT_PATH="${PROJECT_SOURCE_DIR}/topo-check/extractor/topo_extract_python.py"
    )
    topo_set_compiler_flags(topo-lang-python-extractor-fidelity-tests)
    gtest_discover_tests(topo-lang-python-extractor-fidelity-tests DISCOVERY_TIMEOUT 30
        PROPERTIES LABELS "toolchain;lang-python;extractor-fidelity" TIMEOUT 60
        ENVIRONMENT "TOPO_EXTRACT_ROOT=${PROJECT_SOURCE_DIR}")

    # Python *transpile* extractor smoke test — runs the production tool
    # (topo_extract_transpile_python.py) through its real stdin/stdout
    # protocol on small fixtures.
    add_test(NAME topo-extract-python-smoke
        COMMAND ${Python3_EXECUTABLE}
            ${PROJECT_SOURCE_DIR}/topo-check/extractor/smoke_test.py
            -v
    )
    set_tests_properties(topo-extract-python-smoke PROPERTIES
        LABELS "toolchain;lang-python;extractor-smoke"
        TIMEOUT 60
    )
endif()

# Checker E2E tests depend on TopoCheckRunner, which is owned by topo-cli
# (built but not exported — see split-topo-core-deferred-cleanup), so the
# library is not reachable from a standalone topo-lang-python build. The
# tests are skipped here and continue to run from the monorepo / from
# topo-cli once that repo lifts. Mirrors topo-lang-java's check-tests
# gate (deferred behind TOPO_LANG_JAVA_ENABLE_JVM_BACKEND for the same
# reason).
message(STATUS "topo-lang-python tests: checker-e2e suite is gated off in "
               "standalone mode (TopoCheckRunner not exported by topo-core).")

# topo-build-python CLI input-trust unit tests (per-value backendExtras
# validation). Spawns the binary with hand-crafted request JSON files and
# asserts the exit code + stderr label.
#
# Windows-skipped: the suite spawns topo-build-python.exe and every test
# times out at 30s on GHA windows-2022 — same pattern as tpm's
# CLI-integration tests, while ubuntu and macOS finish each in
# milliseconds. Tracked as a MinGW-host subprocess interaction.
if(NOT WIN32)
    add_executable(topo-build-python-input-trust-tests
        unit/PythonBackendExtrasInputTrustTest.cpp
    )
    target_link_libraries(topo-build-python-input-trust-tests PRIVATE
        topo::core::TopoPlatform
        nlohmann_json::nlohmann_json
        GTest::gtest_main
    )
    target_compile_definitions(topo-build-python-input-trust-tests PRIVATE
        TOPO_BUILD_PYTHON_EXE="$<TARGET_FILE:topo-build-python>"
    )
    add_dependencies(topo-build-python-input-trust-tests topo-build-python)
    topo_set_compiler_flags(topo-build-python-input-trust-tests)
    gtest_discover_tests(topo-build-python-input-trust-tests
        PROPERTIES LABELS "toolchain;lang-python" TIMEOUT 30)
endif()

# Functional E2E tests require the topo-build CLI (shipped by topo-core,
# not in topo-core's install set today — see split-topo-core-deferred-cleanup)
# and the in-tree E2eHarness.cpp source. Neither is reachable from this
# standalone build, so the suite is unconditionally skipped here and is
# expected to re-run from the monorepo / from topo-cli once that repo lifts.
message(STATUS "topo-lang-python tests: functional-e2e suite is gated off in "
               "standalone mode (requires topo-build CLI + E2eHarness.cpp).")

# topo-debug-python verification gap. The contract is: when topo-lang-python
# is built, it must ship the topo-debug-python adapter target. The adapter's
# CMakeLists early-returns when Python3 isn't discoverable, leaving the
# target undefined. Surface that absence as an explicit, always-fail ctest
# so CI flags the missing interpreter instead of silently dropping debug
# coverage. (Moved here on the topo-core split; see issue
# topo-debug-python-missing-placeholder-only-makes-sense-in-monorepo.md —
# topo-core can no longer assume topo-lang-python is on the prefix path,
# so this enforcement belongs at the contract holder.)
if(NOT TARGET topo-debug-python)
    add_test(NAME e2e.topo-debug-python.MISSING
        COMMAND ${CMAKE_COMMAND} -E echo
                "topo-debug-python adapter not built: no Python3 \
interpreter found at configure time. Install Python 3 (>=3.8 for the \
pdb fallback; >=3.12 additionally enables --mode=sys-monitoring) and \
re-run cmake.")
    set_tests_properties(e2e.topo-debug-python.MISSING PROPERTIES
        WILL_FAIL TRUE
        LABELS "e2e;topo-debug-python;toolchain"
        TIMEOUT 5)
endif()
