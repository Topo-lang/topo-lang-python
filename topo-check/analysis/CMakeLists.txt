add_library(TopoPythonAnalysis
    PythonAnalysisProvider.cpp
    stub/PythonStubGenerator.cpp
    catalog/PythonUnsafeCatalog.cpp
    extract/PythonSymbolExtractor.cpp
    extract/PythonCallSiteExtractor.cpp
    extract/PythonCallEdgeExtractor.cpp
    extract/PythonSymbolAccessExtractor.cpp
    extract/PythonImportExtractor.cpp
    extract/PythonLSPSymbolExtractor.cpp
    extract/PythonLSPCallSiteExtractor.cpp
    extract/PythonLSPImportExtractor.cpp
    catalog/PythonSafePatterns.cpp
    catalog/PythonSafetyAnalyzer.cpp
)
add_library(topo::lang-python::TopoPythonAnalysis ALIAS TopoPythonAnalysis)

find_path(TOMLPLUSPLUS_INCLUDE_DIR toml++/toml.hpp REQUIRED)

target_include_directories(TopoPythonAnalysis
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-check/analysis>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/extract
        ${CMAKE_CURRENT_SOURCE_DIR}/catalog
        ${CMAKE_CURRENT_SOURCE_DIR}/stub
        ${TOMLPLUSPLUS_INCLUDE_DIR}
)
# topo-core's include path propagates via topo::core::TopoCheck.
# TopoPythonLSP carries the local Python LSP bridge headers (PyrightBridge.h).
target_link_libraries(TopoPythonAnalysis
    PUBLIC topo::core::TopoCheck TopoPythonLSP
    PRIVATE topo::core::TopoPlatform)

# PythonLSPCallSiteExtractor.cpp / PythonLSPImportExtractor.cpp #include
# "analysis/extract/CppCallSiteExtractor.h" / "analysis/extract/CppImportExtractor.h"
# from topo-lang-cpp's source tree. The "analysis/" prefix means we need the
# PARENT of TopoCppAnalysis's source dir on the include path.
if(TARGET topo::lang-cpp::TopoCppAnalysis)
    get_target_property(_cpp_analysis_src
        topo::lang-cpp::TopoCppAnalysis SOURCE_DIR)
    if(_cpp_analysis_src)
        target_include_directories(TopoPythonAnalysis PRIVATE
            "${_cpp_analysis_src}/..")
    endif()
endif()
# TOPO_SOURCE_DIR is consulted by PythonSafePatterns.cpp for source-tree
# catalog discovery (the env var TOPO_PATTERNS_DIR takes precedence). The
# in-source fallback looks for
# <TOPO_SOURCE_DIR>/topo-lang-python/topo-check/analysis/catalog/PythonSafePatterns.toml,
# so in standalone mode we materialise a shim directory in the build tree
# that contains a `topo-lang-python` symlink back to this source root.
# Install-time consumers can override at runtime via TOPO_PATTERNS_DIR.
set(_TOPO_LANG_PYTHON_SHIM "${CMAKE_BINARY_DIR}/topo-source-shim")
file(MAKE_DIRECTORY "${_TOPO_LANG_PYTHON_SHIM}")
if(NOT EXISTS "${_TOPO_LANG_PYTHON_SHIM}/topo-lang-python")
    file(CREATE_LINK "${PROJECT_SOURCE_DIR}"
        "${_TOPO_LANG_PYTHON_SHIM}/topo-lang-python" SYMBOLIC)
endif()
target_compile_definitions(TopoPythonAnalysis PRIVATE
    TOPO_SOURCE_DIR="${_TOPO_LANG_PYTHON_SHIM}"
)
topo_set_compiler_flags(TopoPythonAnalysis)

install(TARGETS TopoPythonAnalysis
    EXPORT topo-lang-pythonTargets
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-check/analysis
)
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/topo-lang-python/topo-check/analysis
    FILES_MATCHING PATTERN "*.h"
)
