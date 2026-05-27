add_library(TopoPythonInit STATIC PythonInitTemplateProvider.cpp)
add_library(topo::lang-python::TopoPythonInit ALIAS TopoPythonInit)

target_include_directories(TopoPythonInit PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-init>
)
target_link_libraries(TopoPythonInit PUBLIC topo::lang::TopoLang)
topo_set_compiler_flags(TopoPythonInit)

install(TARGETS TopoPythonInit
    EXPORT topo-lang-pythonTargets
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-init
)
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-init
    FILES_MATCHING PATTERN "*.h"
)
