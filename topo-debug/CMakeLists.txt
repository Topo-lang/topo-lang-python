# topo-debug-python — DAP adapter.
#
# Python is interpreted, so this directory ships source — no compilation step.
# CMake's role here is to:
#   1. Locate a Python 3 interpreter (Python3_EXECUTABLE).
#   2. Verify `debugpy` is importable under that interpreter at configure time
# ( debugpy is an external runtime dependency; missing it is
#      a soft skip, not a configure failure — keeps `cmake` green on hosts
#      that haven't installed it).
#   3. Stage `main.py` next to the launcher script so the configured shim
#      `topo-debug-python` has a stable path to invoke.
#   4. Materialise the launcher script(s) (POSIX shell + Windows .cmd) into
#      the build tree, baking in the resolved Python path.
#   5. Copy the fixture to the build tree so the e2e tests in
#      topo-core/test/CMakeLists.txt can $<TARGET_FILE>-style reference it.
#
# We gate on `TARGET topo-debug-python` (a custom target) so the e2e test
# wiring in topo-core/test/CMakeLists.txt can conditionally pull us in.

find_package(Python3 COMPONENTS Interpreter QUIET)

if(NOT Python3_FOUND)
    message(STATUS "topo-debug-python: Python3 interpreter not found — "
                   "skipping topo-debug-python build; tests "
                   "will be disabled.")
    return()
endif()

# debugpy enables the full DAP path (snapshot/replay, Web Render). A
# stdlib `pdb` fallback was added for basic variable reads, so the
# adapter is now USEFUL even without debugpy — we therefore build the
# launcher whenever Python3 is present and only *record* debugpy
# availability so the e2e wiring can split DAP-only tests from
# fallback-capable ones. (Fixes the prior gap where the basic-read
# tests vanished entirely when debugpy was absent.)
execute_process(
    COMMAND "${Python3_EXECUTABLE}" -c "import debugpy"
    RESULT_VARIABLE _DEBUGPY_PROBE
    OUTPUT_QUIET ERROR_QUIET)
if(_DEBUGPY_PROBE EQUAL 0)
    set(TOPO_DEBUG_PYTHON_HAS_DEBUGPY ON
        CACHE INTERNAL "topo-debug-python: debugpy importable (DAP path).")
    message(STATUS "topo-debug-python: using ${Python3_EXECUTABLE} "
                   "(debugpy ok — full DAP path)")
else()
    set(TOPO_DEBUG_PYTHON_HAS_DEBUGPY OFF
        CACHE INTERNAL "topo-debug-python: debugpy importable (DAP path).")
    message(STATUS "topo-debug-python: ${Python3_EXECUTABLE} cannot import "
                   "debugpy — building with stdlib pdb fallback only "
                   "(basic variable reads; snapshot/replay/Web Render "
                   "disabled). To enable the full path: "
                   "`${Python3_EXECUTABLE} -m pip install debugpy`.")
endif()

# Stage the Python adapter package into the build tree. We copy rather than
# referencing the source tree so all of <Python3_EXECUTABLE> <main.py> ...
# points at a single, dependency-free location regardless of where the
# repository lives.
set(_STAGE_DIR "${CMAKE_CURRENT_BINARY_DIR}/topo_debug_python")
file(MAKE_DIRECTORY "${_STAGE_DIR}")
configure_file(topo_debug_python/__init__.py
    "${_STAGE_DIR}/__init__.py" COPYONLY)
configure_file(topo_debug_python/main.py
    "${_STAGE_DIR}/main.py" COPYONLY)
configure_file(topo_debug_python/pdb_fallback.py
    "${_STAGE_DIR}/pdb_fallback.py" COPYONLY)

set(TOPO_DEBUG_PYTHON_MAIN "${_STAGE_DIR}/main.py")

# Generate the launcher shim. The launcher path lives at the build-tree root
# of this directory so callers do `--adapter $(...)/topo-lang-python/topo-debug/topo-debug-python`.
set(_LAUNCHER "${CMAKE_CURRENT_BINARY_DIR}/topo-debug-python")
configure_file(topo-debug-python.in "${_LAUNCHER}" @ONLY)
file(CHMOD "${_LAUNCHER}"
    PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE
                GROUP_READ GROUP_EXECUTE
                WORLD_READ WORLD_EXECUTE)

if(WIN32)
    configure_file(topo-debug-python.cmd.in
        "${CMAKE_CURRENT_BINARY_DIR}/topo-debug-python.cmd" @ONLY)
endif()

# Custom target so e2e CTest gating via `if(TARGET topo-debug-python)` works.
# We use a distinct name from the launcher script file to avoid a Ninja
# dependency cycle (CMake creates `CMakeFiles/<target>` artefacts that would
# otherwise collide with a same-named output path).
add_custom_target(topo-debug-python-launcher ALL
    DEPENDS "${_LAUNCHER}" "${TOPO_DEBUG_PYTHON_MAIN}"
            "${_STAGE_DIR}/pdb_fallback.py")

# Conventional alias the e2e tests in topo-core/test/CMakeLists.txt query.
# CMake doesn't support alias-of-custom-target, so we register the same target
# twice under both names via INTERFACE indirection — simplest path is to make
# both visible by tagging the existing target as imported from this scope.
# In practice every consumer just calls `if(TARGET topo-debug-python)`, so we
# add a no-op interface library with that name that depends on the launcher.
add_library(topo-debug-python INTERFACE)
add_dependencies(topo-debug-python topo-debug-python-launcher)

# Export the launcher path so topo-core/test/CMakeLists.txt can read it.
set(TOPO_DEBUG_PYTHON_LAUNCHER "${_LAUNCHER}"
    CACHE INTERNAL "Path to the topo-debug-python launcher script.")

add_subdirectory(test)
