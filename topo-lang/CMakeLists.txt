add_library(TopoPythonPlugin
    PythonPlugin.cpp
)
add_library(topo::lang-python::TopoPythonPlugin ALIAS TopoPythonPlugin)

target_include_directories(TopoPythonPlugin PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-lang>
)
# The per-component PRIVATE include paths the monorepo set are unnecessary
# in standalone mode — each linked target already carries its own headers
# on its PUBLIC interface.
target_link_libraries(TopoPythonPlugin PUBLIC
    topo::lang::TopoLang
    TopoPythonAnalysis
    TopoPythonCheck
    TopoPythonTranspile
    TopoPythonLSP
    TopoPythonInit
)
topo_set_compiler_flags(TopoPythonPlugin)

install(TARGETS TopoPythonPlugin
    EXPORT topo-lang-pythonTargets
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-lang
)
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-lang
    FILES_MATCHING PATTERN "*.h"
)
