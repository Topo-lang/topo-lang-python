add_library(TopoPythonTranspile
    PythonEmitter.cpp
)
add_library(topo::lang-python::TopoPythonTranspile ALIAS TopoPythonTranspile)

target_include_directories(TopoPythonTranspile PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-transpile>
)
# topo-core/include propagates via topo::core::TopoTranspile.
target_link_libraries(TopoPythonTranspile PUBLIC topo::core::TopoTranspile)
topo_set_compiler_flags(TopoPythonTranspile)

install(TARGETS TopoPythonTranspile
    EXPORT topo-lang-pythonTargets
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-transpile
)
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-transpile
    FILES_MATCHING PATTERN "*.h"
)
