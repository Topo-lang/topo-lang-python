add_library(TopoPythonLSP
    PyrightBridge.cpp
)
add_library(topo::lang-python::TopoPythonLSP ALIAS TopoPythonLSP)

target_include_directories(TopoPythonLSP PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-lsp>
)
target_link_libraries(TopoPythonLSP PUBLIC topo::core::TopoLSPBridge)
topo_set_compiler_flags(TopoPythonLSP)

install(TARGETS TopoPythonLSP
    EXPORT topo-lang-pythonTargets
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-lsp
)
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-lsp
    FILES_MATCHING PATTERN "*.h"
)
