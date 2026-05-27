# topo-lang-python — Python Language Support

Python language analysis, LSP bridge, check-only build orchestrator,
transpile emitter, and plugin registration for the Topo toolchain. Python
has no native compilation driver: `topo-build-python` is a C++ executable
that orchestrates check + script-based extraction.

Upstream packages:
- [`topo-core`](https://github.com/topo-lang/topo-core) — declaration
  language core, check/analysis/transpile libraries
- [`topo-lang`](https://github.com/topo-lang/topo-lang) — plugin
  registration framework

## Structure

Second-level directories are named after the `topo-<tool>` they serve,
so the mapping from code to top-level component is explicit.

| Directory               | Serves        | Purpose |
|-------------------------|---------------|---------|
| `runtime/`              | user code     | Python source-only runtime payload (no build step) |
| `topo-check/analysis/`  | topo-check    | `PythonAnalysisProvider` + regex / Pyright LSP-backed extractors + safety catalog + stub generator |
| `topo-check/runner/`    | topo-check    | `PythonCheckRunner` — language-specific check orchestration |
| `topo-check/extractor/` | topo-check    | `topo-extract-python` Python-script launcher (`topo_extract_transpile_python.py` for transpile, `topo_extract_python.py` for L2 containment) |
| `topo-build/`           | topo-build    | `topo-build-python` C++ executable — check-only build orchestrator |
| `topo-init/`            | topo-init     | Python project template provider |
| `topo-lsp/`             | topo-lsp      | `PyrightBridge` — proxies Pyright for IDE integration |
| `topo-transpile/`       | topo-transpile | `PythonEmitter` — TranspileModel → Python source |
| `topo-debug/`           | topo-debug    | `topo-debug-python` DAP launcher (uses `debugpy` if installed, falls back to stdlib `pdb`) |
| `topo-profile/`         | topo-profile  | Python span-emitter fixture for the topo-profile demo |
| `topo-lang/`            | topo-lang     | `PythonPlugin` — registers all components with the plugin framework |
| `test/`                 | —             | C++ unit + extractor-fidelity + extractor-smoke suites |
| `examples/`             | —             | Quickstart project |

## Build

```sh
# Build & install topo-core and topo-lang first, into a shared prefix.
# Then:
cmake -S . -B build -G Ninja \
    -DCMAKE_PREFIX_PATH=<topo-install-prefix> \
    -DCMAKE_TOOLCHAIN_FILE=$VCPKG_ROOT/scripts/buildsystems/vcpkg.cmake
cmake --build build
```

## Consume via `find_package`

```cmake
find_package(topo-lang-python CONFIG REQUIRED)

# Available imported targets:
#   topo::lang-python::TopoPythonAnalysis
#   topo::lang-python::TopoPythonCheck
#   topo::lang-python::TopoPythonInit
#   topo::lang-python::TopoPythonLSP
#   topo::lang-python::TopoPythonPlugin
#   topo::lang-python::TopoPythonTranspile

target_link_libraries(my_tool PRIVATE topo::lang-python::TopoPythonAnalysis)
```

The `topo-build-python` executable is installed into `${CMAKE_INSTALL_BINDIR}`.

## Tests

```sh
ctest --test-dir build --output-on-failure
```

Runtime soft-dependencies that gate individual suites:
- `python3` (extractor-fidelity + extractor-smoke + topo-debug-python +
  topo-profile fixture).
- `pyright-langserver` (Pyright LSP-backed extractors and L2 deep
  containment — runtime only; the C++ build never needs it).
- `debugpy` (full DAP path in `topo-debug-python`; absent → stdlib `pdb`
  fallback).

The functional-e2e and checker-e2e suites require the `topo-build` CLI
and the `TopoCheckRunner` library, both owned by other repos that have
not finished lifting yet; those suites are skipped in standalone mode.
