# fixtures for topo-debug-python.
#
# Python is interpreted, so the fixture is just a source file copied into
# the build tree. The e2e tests in topo-core/test/CMakeLists.txt reference
# it via TOPO_DEBUG_PYTHON_TINY_VECTOR.
configure_file(fixtures/tiny_vector.py
    "${CMAKE_CURRENT_BINARY_DIR}/fixtures/tiny_vector.py" COPYONLY)

set(TOPO_DEBUG_PYTHON_TINY_VECTOR
    "${CMAKE_CURRENT_BINARY_DIR}/fixtures/tiny_vector.py"
    CACHE INTERNAL "Path to the tiny_vector.py fixture for topo-debug-python.")

# stdlib-bridge default-summary fixture (uuid /
# decimal128 / ndarray / time_ns host values).
configure_file(fixtures/stdlib_bridge.py
    "${CMAKE_CURRENT_BINARY_DIR}/fixtures/stdlib_bridge.py" COPYONLY)
set(TOPO_DEBUG_PYTHON_STDLIB_BRIDGE
    "${CMAKE_CURRENT_BINARY_DIR}/fixtures/stdlib_bridge.py"
    CACHE INTERNAL "Path to the stdlib_bridge.py fixture.")

# numpy is optional; the ndarray summary case is registered only when the
# configured interpreter can import it (explicit configure-time gate, not
# a silent runtime skip). uuid/decimal/time_ns use only the stdlib.
if(Python3_EXECUTABLE)
    execute_process(
        COMMAND "${Python3_EXECUTABLE}" -c "import numpy"
        RESULT_VARIABLE _NUMPY_PROBE
        OUTPUT_QUIET ERROR_QUIET)
    if(_NUMPY_PROBE EQUAL 0)
        set(TOPO_DEBUG_PYTHON_HAS_NUMPY ON CACHE INTERNAL
            "numpy importable (ndarray summary e2e enabled).")
    else()
        set(TOPO_DEBUG_PYTHON_HAS_NUMPY OFF CACHE INTERNAL
            "numpy importable (ndarray summary e2e enabled).")
        message(STATUS "topo-debug-python: numpy not importable — "
                       "ndarray-summary e2e disabled (uuid/decimal128/"
                       "time_ns still covered).")
    endif()
endif()

# project-level Python fixtures (parallel to cpp/rust/java
# project_simple|project_multi). Unlike tiny_vector.py (a single .py file
# debugged directly), these fixtures exercise the declaration-driven path
# end-to-end: `topo build` parses main.topo, runs topo-build-python's
# completeness check, writes main.topo-dbg.json (the view registry), and
# main.py stays a plain script. Each fixture is staged into the build tree
# at configure time so `topo build` writes its outputs there rather than
# into the source checkout.
set(_PROJECT_SIMPLE_PY_SRC ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/project_simple)
set(TOPO_DEBUG_PYTHON_PROJECT_SIMPLE_DIR
    ${CMAKE_CURRENT_BINARY_DIR}/fixtures/project_simple
    CACHE INTERNAL "Project-simple Python fixture build dir")
file(MAKE_DIRECTORY ${TOPO_DEBUG_PYTHON_PROJECT_SIMPLE_DIR})
configure_file(${_PROJECT_SIMPLE_PY_SRC}/Topo.toml
    ${TOPO_DEBUG_PYTHON_PROJECT_SIMPLE_DIR}/Topo.toml COPYONLY)
configure_file(${_PROJECT_SIMPLE_PY_SRC}/main.topo
    ${TOPO_DEBUG_PYTHON_PROJECT_SIMPLE_DIR}/main.topo COPYONLY)
configure_file(${_PROJECT_SIMPLE_PY_SRC}/main.py
    ${TOPO_DEBUG_PYTHON_PROJECT_SIMPLE_DIR}/main.py COPYONLY)

set(_PROJECT_MULTI_PY_SRC ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/project_multi)
set(TOPO_DEBUG_PYTHON_PROJECT_MULTI_DIR
    ${CMAKE_CURRENT_BINARY_DIR}/fixtures/project_multi
    CACHE INTERNAL "Project-multi Python fixture build dir")
file(MAKE_DIRECTORY ${TOPO_DEBUG_PYTHON_PROJECT_MULTI_DIR})
configure_file(${_PROJECT_MULTI_PY_SRC}/Topo.toml
    ${TOPO_DEBUG_PYTHON_PROJECT_MULTI_DIR}/Topo.toml COPYONLY)
configure_file(${_PROJECT_MULTI_PY_SRC}/main.topo
    ${TOPO_DEBUG_PYTHON_PROJECT_MULTI_DIR}/main.topo COPYONLY)
configure_file(${_PROJECT_MULTI_PY_SRC}/main.py
    ${TOPO_DEBUG_PYTHON_PROJECT_MULTI_DIR}/main.py COPYONLY)
