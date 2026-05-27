add_executable(topo-build-python
    main.cpp
)
# TopoPythonAnalysis PUBLIC-propagates topo::core::TopoCheck → TopoSema /
# TopoAnalysis; topo::core::TopoBuildLib PUBLIC-propagates TopoPlatform /
# TopoSema / topo::lang::TopoLang.
target_link_libraries(topo-build-python PRIVATE
    TopoPythonAnalysis
    topo::core::TopoBuildLib
    nlohmann_json::nlohmann_json
)
# `analysis/extract/PythonSymbolExtractor.h` is referenced by main.cpp with
# the `analysis/...` prefix, so the include root is topo-lang-python/topo-check/.
target_include_directories(topo-build-python PRIVATE
    ${CMAKE_SOURCE_DIR}/topo-check
)
topo_set_compiler_flags(topo-build-python)

install(TARGETS topo-build-python
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
