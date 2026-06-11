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
    unit/PythonImportExtractorTest.cpp
    unit/PythonEmitterStdlibTest.cpp
    unit/PythonSafePatternsLoadTest.cpp
    unit/PythonAnalysisProviderTest.cpp
)
# TopoPythonAnalysis PUBLIC-propagates TopoPythonLSP → topo::core::TopoLSPBridge
# → topo::core::TopoPlatform. TopoPythonTranspile brings in PythonEmitter
# (PUBLIC-propagates topo::core::TopoTranspile) for the stdlib emitter tests.
target_link_libraries(topo-lang-python-tests PRIVATE
    TopoPythonAnalysis TopoPythonTranspile
    GTest::gtest_main
)
target_include_directories(topo-lang-python-tests PRIVATE
    ${PROJECT_SOURCE_DIR}/topo-check       # analysis/stub/*.h + analysis/extract/*.h
    ${PROJECT_SOURCE_DIR}/topo-transpile   # for PythonEmitter.h
)
topo_set_compiler_flags(topo-lang-python-tests)
gtest_discover_tests(topo-lang-python-tests DISCOVERY_TIMEOUT 30
    PROPERTIES LABELS "toolchain\;lang-python\;stdlib" TIMEOUT 60)

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
        PROPERTIES LABELS "toolchain\;lang-python\;extractor-fidelity" TIMEOUT 60
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

# Runtime toolchain-locator unit tests (pure-stdlib unittest; fake
# binaries staged in temp dirs, no real toolchain needed). Pins the
# canonical resolution order: strict TOPO_BIN_DIR > PATH > sibling build
# trees, build-no-llvm excluded. The topo package __init__ imports
# tomllib, so Python 3.11 is the floor.
if(Python3_FOUND AND Python3_VERSION VERSION_GREATER_EQUAL 3.11)
    add_test(NAME py-toolchain-resolution
        COMMAND ${Python3_EXECUTABLE} -m unittest -v
                test_platform test_toolchain_resolution
        WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}/runtime/test
    )
    set_tests_properties(py-toolchain-resolution PROPERTIES
        LABELS "toolchain;lang-python"
        TIMEOUT 60
    )
else()
    message(STATUS "topo-lang-python tests: py-toolchain-resolution skipped — "
                   "needs Python3 >= 3.11 (the topo package imports tomllib).")
endif()

# Checker E2E suite — lives in check-e2e/ as a self-gating, idempotent file
# so a meta-style super-build can re-add it after its topo-cli sibling
# configures; see check-e2e/CMakeLists.txt for the gating rationale. This
# inline add covers the standalone path (installed topo-cli on
# CMAKE_PREFIX_PATH) and self-skips with a STATUS message otherwise.
add_subdirectory(check-e2e)

# topo-build-python CLI input-trust unit tests (per-value backendExtras
# validation). Spawns the binary with hand-crafted request JSON files and
# asserts the exit code + stderr label.
#
# Previously Windows-skipped: the suite spawns topo-build-python.exe via
# topo::platform::runProcessCapture, which hung on GHA windows-2022 (reproc
# exit-detection for MinGW children). Fixed in topo-core (raw-Win32 capture) +
# the build-time gcc PATH-prepend in the Test step so the spawned MinGW tool
# loads. Runs on all three platforms now.
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
    PROPERTIES LABELS "toolchain\;lang-python" TIMEOUT 30)

# Functional E2E suite — drives the real topo-build CLI funnel against the
# fixtures under fixtures/e2e (exit-code propagation, integrated-check
# wiring, --no-verify/--no-check interplay, diagnostic surfacing). Needs
# the topo-build + topo-check executables from topo-cli, a package
# DOWNSTREAM of this one. Two paths in:
#   * super-build (NOT PROJECT_IS_TOP_LEVEL) — the bare sibling targets
#     resolve at generate time, after the topo-cli subdir is added;
#   * standalone — an installed topo-cli on CMAKE_PREFIX_PATH provides the
#     imported topo::cli::topo-build / topo::cli::topo-check executables
#     (the verify-l2 CI job's surface). Self-skips with STATUS otherwise.
if(NOT PROJECT_IS_TOP_LEVEL)
    set(_topo_func_build_tgt topo-build)
    set(_topo_func_check_tgt topo-check)
else()
    if(NOT TARGET topo::cli::topo-build)
        find_package(topo-cli CONFIG QUIET)
    endif()
    if(TARGET topo::cli::topo-build AND TARGET topo::cli::topo-check)
        set(_topo_func_build_tgt topo::cli::topo-build)
        set(_topo_func_check_tgt topo::cli::topo-check)
    endif()
endif()
if(DEFINED _topo_func_build_tgt)
    add_executable(topo-lang-python-functional-tests
        e2e/PythonFunctionalTests.cpp
    )
    target_link_libraries(topo-lang-python-functional-tests PRIVATE
        topo::core::TopoPlatform
        GTest::gtest_main
    )
    # TOPO_FUNC_E2E_TOOL_DIRS is ';'-joined; the fixture rejoins with the
    # platform PATH separator and prepends once per process: topo-build
    # dispatches by [build].language to topo-build-python and spawns
    # topo-check, so both tool dirs must be reachable from PATH.
    target_compile_definitions(topo-lang-python-functional-tests PRIVATE
        TOPO_BUILD_EXE="$<TARGET_FILE:${_topo_func_build_tgt}>"
        TOPO_PYTHON_FIXTURES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/e2e"
        TOPO_FUNC_E2E_TOOL_DIRS="$<TARGET_FILE_DIR:topo-build-python>$<SEMICOLON>$<TARGET_FILE_DIR:${_topo_func_check_tgt}>"
    )
    # topo-build / topo-check may be imported executables here — CMake
    # disallows add_dependencies on those; the $<TARGET_FILE:...> defines
    # carry the file-level dependency. topo-build-python is in-tree.
    add_dependencies(topo-lang-python-functional-tests topo-build-python)
    topo_set_compiler_flags(topo-lang-python-functional-tests)
    gtest_discover_tests(topo-lang-python-functional-tests DISCOVERY_TIMEOUT 30
        PROPERTIES LABELS "e2e\;functional\;lang-python" TIMEOUT 120)
    unset(_topo_func_build_tgt)
    unset(_topo_func_check_tgt)
else()
    message(STATUS "topo-lang-python tests: functional-e2e suite is gated off in "
                   "standalone mode (topo-cli config providing topo-build/topo-check "
                   "not on CMAKE_PREFIX_PATH).")
endif()

# topo-debug-python verification gap. The contract is: when topo-lang-python
# is built, it must ship the topo-debug-python adapter target. The adapter's
# CMakeLists early-returns when Python3 isn't discoverable, leaving the
# target undefined. Surface that absence as an explicit, always-fail ctest
# so CI flags the missing interpreter instead of silently dropping debug
# coverage. (Moved here on the topo-core split: topo-core can no longer
# assume topo-lang-python is on the prefix path, so this enforcement
# belongs at the contract holder rather than as a placeholder upstream.)
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
