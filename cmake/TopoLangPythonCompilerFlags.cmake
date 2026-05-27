# TopoLangPythonCompilerFlags.cmake — standalone compiler-flag helper for topo-lang-python.

if(NOT WIN32)
    set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)
    if(APPLE)
        set(CMAKE_MACOSX_RPATH ON)
    endif()
endif()

set(TOPO_LANG_PYTHON_SANITIZER "" CACHE STRING
    "Enable sanitizers (address, undefined, thread, memory)")

function(topo_lang_python_apply_sanitizer target)
    if(NOT TOPO_LANG_PYTHON_SANITIZER)
        return()
    endif()
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
        target_compile_options(${target}
            PRIVATE -fsanitize=${TOPO_LANG_PYTHON_SANITIZER} -fno-omit-frame-pointer)
        target_link_options(${target}
            PRIVATE -fsanitize=${TOPO_LANG_PYTHON_SANITIZER})
    endif()
endfunction()

function(topo_set_compiler_flags target)
    target_compile_features(${target} PUBLIC cxx_std_17)
    set_target_properties(${target} PROPERTIES CXX_EXTENSIONS OFF)
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
        target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    elseif(MSVC)
        target_compile_options(${target} PRIVATE /W4)
    endif()
    topo_lang_python_apply_sanitizer(${target})
endfunction()

function(topo_set_llvm_flags target)
    # topo-lang-python doesn't link LLVM — Python has no LLVM backend in
    # Topo. The helper exists for symmetry with topo-lang-cpp/topo-lang-rust
    # so vendored subdir CMakeLists that conditionally call it still configure.
    topo_set_compiler_flags(${target})
endfunction()

if(NOT COMMAND topo_apply_std_pch)
    function(topo_apply_std_pch target)
        # PCH stub — no-op in standalone topo-lang-python.
    endfunction()
endif()
