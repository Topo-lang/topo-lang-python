@echo off
rem Windows launcher for topo-extract-python (transpile path).
rem Mirror of the POSIX `topo-extract-python` shim — same PATH-resolution
rem contract TranspileDriver expects. The script lives either alongside
rem this launcher (in-tree) or under a topo-extract-python-tool\ sibling
rem dir (after CMake staging).

setlocal
set "_dir=%~dp0"
if not defined PYTHON3 (
    set "PYTHON3=python"
)

if exist "%_dir%topo-extract-python-tool\topo_extract_transpile_python.py" (
    "%PYTHON3%" "%_dir%topo-extract-python-tool\topo_extract_transpile_python.py" %*
    exit /b %ERRORLEVEL%
)

"%PYTHON3%" "%_dir%topo_extract_transpile_python.py" %*
exit /b %ERRORLEVEL%
