# topo-extract-python (transpile path): Python source → TranspileModule.
#
# TranspileDriver spawns this by the bare name `topo-extract-python`
# (PATH lookup via execvp/CreateProcess), same contract the compiled
# topo-extract-cpp / topo-extract-rust binaries and the Node-backed
# topo-extract-typescript launcher all satisfy. The tool is a Python
# script, so a launcher shim of that exact name execs `python3 …script`
# from a staged copy. Staged into topo-build-python's binary directory so
# a single binary directory holds the whole Python toolset.
#
# Sister tool note: the L2 containment extractor in this same source
# directory (`topo_extract_python.py`) is resolved by absolute path from
# PythonSafetyAnalyzer.cpp (not via PATH and not via this stage step),
# so the two extractors with overlapping names do not collide.
#
# Implemented as a standalone custom target (not a POST_BUILD on
# topo-build-python — add_custom_command(TARGET) only works in the
# directory that created the target). Consumers add_dependencies() on
# `topo-extract-python-stage` and on `topo-build-python` (for its
# binary dir to exist).

if(NOT TARGET topo-build-python)
    return()
endif()

set(_extractSrc ${CMAKE_CURRENT_SOURCE_DIR})
set(_binDir $<TARGET_FILE_DIR:topo-build-python>)
set(_extractDst ${_binDir}/topo-extract-python-tool)

if(WIN32)
    set(_launcherName topo-extract-python.cmd)
else()
    set(_launcherName topo-extract-python)
endif()

# Copy the script + launcher into a sibling subdir, then place the
# launcher alongside the build binary so a single PATH entry resolves
# the bare name. The script body lives in the …-tool/ subdir to match
# the typescript layout and to keep the launcher file unambiguous.
add_custom_target(topo-extract-python-stage ALL
    COMMAND ${CMAKE_COMMAND} -E copy
        ${_extractSrc}/topo_extract_transpile_python.py
        ${_extractDst}/topo_extract_transpile_python.py
    COMMAND ${CMAKE_COMMAND} -E copy
        ${_extractSrc}/${_launcherName}
        ${_extractDst}/${_launcherName}
    COMMAND ${CMAKE_COMMAND} -E copy
        ${_extractSrc}/${_launcherName}
        ${_binDir}/${_launcherName}
    COMMENT "Staging topo-extract-python (transpile extractor)"
    VERBATIM
)
add_dependencies(topo-extract-python-stage topo-build-python)

if(NOT WIN32)
    # POSIX permission bit doesn't survive cmake -E copy from a checked-in
    # +x source, so re-mark explicitly here. Both the staged copy and the
    # alongside-binary launcher need the bit so PATH resolution can exec.
    add_custom_command(TARGET topo-extract-python-stage POST_BUILD
        COMMAND chmod +x
            ${_binDir}/${_launcherName}
            ${_extractDst}/${_launcherName}
        COMMENT "Marking topo-extract-python launcher executable"
        VERBATIM
    )
endif()
