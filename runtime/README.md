# topo-lang-python/runtime

Standard-scenario Python needs no runtime library: hand-written `.topo`
declarations are validated at check time, nothing is linked.

This directory also hosts the **topo-app** Python package (`topo/`) —
the quick-start framework's Python projection (vendored vertical-slice
PoC). It is a separate, optional product layer: import it to write
handlers/flows in idiomatic Python and get a round-trippable `.topo`
contract plus zero-declaration `topo-check`, without writing `.topo` by
hand.

- `topo/` — the importable package (`import topo`)
- `examples/order_pipeline.py` — a runnable 3-stage flow
- `test/test_vertical_slice.py` — acceptance tests (needs the built
  toolchain; set `TOPO_BIN_DIR` or have a sibling `build-no-llvm`/`build`)

The package consumes the existing toolchain (`topo`, `topo-check`); it
reimplements no parsing or checking. Dependency direction stays
`topo-app -> toolchain`, never reversed.
