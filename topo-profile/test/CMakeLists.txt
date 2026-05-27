# Python span-emitter fixture for topo-profile.
#
# Python is interpreted, so the "fixture" is a wrapper script that calls
# `${Python3_EXECUTABLE} spans_demo.py`. The wrapper is a single-line
# launcher placed in the build tree; the .py source is copied next to it
# so `topo-profile` can spawn the wrapper like any other executable.

find_package(Python3 COMPONENTS Interpreter QUIET)
if(NOT Python3_FOUND)
    message(STATUS "topo-lang-python/topo-profile: Python3 not found; "
                   "skipping span fixture.")
    return()
endif()

set(_FIXTURE_SRC "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/spans_demo.py")
set(_FIXTURE_DIR "${CMAKE_CURRENT_BINARY_DIR}")
set(_FIXTURE_PY  "${_FIXTURE_DIR}/spans_demo.py")
set(_WRAPPER     "${_FIXTURE_DIR}/spans_demo_py.sh")

configure_file("${_FIXTURE_SRC}" "${_FIXTURE_PY}" COPYONLY)

file(GENERATE OUTPUT "${_WRAPPER}"
     CONTENT "#!/bin/sh\nexec \"${Python3_EXECUTABLE}\" \"${_FIXTURE_PY}\" \"$@\"\n")
add_custom_command(
    OUTPUT "${_WRAPPER}.stamp"
    COMMAND ${CMAKE_COMMAND} -E touch "${_WRAPPER}.stamp"
    COMMAND chmod +x "${_WRAPPER}"
    DEPENDS "${_FIXTURE_PY}" "${_WRAPPER}"
    VERBATIM)

add_custom_target(spans_demo_py ALL DEPENDS "${_WRAPPER}.stamp")
set_target_properties(spans_demo_py PROPERTIES
    TOPO_FIXTURE_PATH "${_WRAPPER}")
