"""topo-app — Topo's quick-start framework, Python projection.

Write idiomatic Python handlers/flows; the framework produces a
round-trippable ``.topo`` contract that the existing Topo toolchain
consumes and checks. The user writes no ``.topo`` by hand.

Public surface (the language-agnostic topo-app API projected to Python):

    import topo

    app = topo.App("orders")

    @app.handler
    def parse(raw: str) -> topo.Record[("id", int), ("amount", float)]:
        ...

    app.flow("pipeline", parse, validate, persist)

    cfg = topo.config(app)
    cfg.snapshot()            # whole graph, one place
    cfg.emit_topo("o.topo")   # the round-trippable .topo view
    topo.check(app, ["app.py"])  # zero-declaration topo-check
"""

from ._config_model import (
    DevInternalItem,
    DevInternalRegistry,
    ImpactLevel,
    ItemPolicy,
)
from ._record import Field, Record
from .app import App, parallel
from .check import CheckResult, check
from .config import Config, ProductConfig, config

__all__ = [
    "App",
    "parallel",
    "Record",
    "Field",
    "config",
    "Config",
    "ProductConfig",
    "ItemPolicy",
    "ImpactLevel",
    "DevInternalRegistry",
    "DevInternalItem",
    "check",
    "CheckResult",
]
