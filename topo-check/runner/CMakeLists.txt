add_library(TopoPythonCheck STATIC PythonCheckRunner.cpp)
add_library(topo::lang-python::TopoPythonCheck ALIAS TopoPythonCheck)

target_include_directories(TopoPythonCheck PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-check/runner>
)
target_link_libraries(TopoPythonCheck PUBLIC topo::lang::TopoLang)
topo_set_compiler_flags(TopoPythonCheck)

install(TARGETS TopoPythonCheck
    EXPORT topo-lang-pythonTargets
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-check/runner
)
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-check/runner
    FILES_MATCHING PATTERN "*.h"
)
