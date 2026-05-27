cmake_minimum_required(VERSION 3.20)

project(topo-lang-python
    VERSION 0.1.0
    DESCRIPTION "Topo Python language support — check, build orchestrator, transpile emitter, LSP bridge, plugin, Python runtime + extractors"
    HOMEPAGE_URL "https://github.com/topo-lang/topo-lang-python"
    LANGUAGES CXX
)

# topo-lang-python: Python language support.
#
# Layering:
#   - C++ libs (analysis, runner, lsp bridge, transpile emitter, init,
#     plugin) and the C++ topo-build-python orchestrator ship by default;
#     they only need topo-core + topo-lang + a host C++ compiler.
#   - The runtime payload (Python sources under runtime/) and the Python-
#     script-based topo-extract-python / topo-debug-python launchers are
#     interpreted at runtime, not built. Their staging (which only fires
#     when a Python3 interpreter is available) is part of the default
#     build — Python is a soft prerequisite, not a gated feature.
#
# Python has no LLVM or JVM backend coupling, so there is no
# TOPO_LANG_PYTHON_ENABLE_<backend> gate.

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(TopoLangPythonCompilerFlags)

# ── Upstream Topo packages ────────────────────────────
find_package(topo-core CONFIG REQUIRED)
find_package(topo-lang CONFIG REQUIRED)

# ── External dependencies ─────────────────────────────
find_package(nlohmann_json CONFIG REQUIRED)

# ── Subdirs ───────────────────────────────────────────
# Order: lsp/transpile/runner first since analysis links them.
add_subdirectory(topo-lsp)
add_subdirectory(topo-transpile)
add_subdirectory(topo-check/runner)
add_subdirectory(topo-check/analysis)
add_subdirectory(topo-build)
add_subdirectory(topo-init)
add_subdirectory(topo-lang)

# The transpile extractor is a Python-script launcher staged alongside the
# topo-build-python binary directory. The staging step early-returns if
# topo-build-python isn't defined (e.g. when this subdir is included
# directly), so order matters: must come AFTER topo-build.
add_subdirectory(topo-check/extractor)

# topo-debug-python materializes the DAP launcher when a Python3 interpreter
# is discoverable; debugpy is a soft dependency (stdlib `pdb` fallback exists).
# topo-profile fixture spawns `python3 spans_demo.py`; it likewise soft-skips.
add_subdirectory(topo-debug)
add_subdirectory(topo-profile)

# ── Tests ─────────────────────────────────────────────
option(TOPO_LANG_PYTHON_BUILD_TESTS "Build topo-lang-python tests" ON)
if(TOPO_LANG_PYTHON_BUILD_TESTS AND PROJECT_IS_TOP_LEVEL)
    enable_testing()
    include(FetchContent)
    FetchContent_Declare(googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        GIT_TAG v1.15.2)
    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)
    include(GoogleTest)
    add_subdirectory(test)
endif()

# ── Install / export ──────────────────────────────────
install(EXPORT topo-lang-pythonTargets
    FILE topo-lang-pythonTargets.cmake
    NAMESPACE topo::lang-python::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/topo-lang-python
)

configure_package_config_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/cmake/topo-lang-pythonConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/topo-lang-pythonConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/topo-lang-python
)

write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/topo-lang-pythonConfigVersion.cmake
    VERSION       ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion
)

install(FILES
    ${CMAKE_CURRENT_BINARY_DIR}/topo-lang-pythonConfig.cmake
    ${CMAKE_CURRENT_BINARY_DIR}/topo-lang-pythonConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/topo-lang-python
)
