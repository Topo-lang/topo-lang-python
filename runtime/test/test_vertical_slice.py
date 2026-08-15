"""End-to-end acceptance for the topo-app Python vertical slice.

Each test maps to a plan task's acceptance criterion. Requires the
toolchain binaries (``topo``, ``topo-check``); set ``TOPO_BIN_DIR``,
have them on PATH, or have a sibling ``build`` / ``build-asan`` tree.

Run: ``python3 -m unittest discover -s topo-lang-python/runtime/test``
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import topo  # noqa: E402

OrderRec = topo.Record[("id", int), ("amount", float)]


def build_app(namespace="orders"):
    app = topo.App(namespace)

    @app.handler
    def parse(raw: str) -> OrderRec:
        return (len(raw), 1.0)

    @app.handler
    def validate(order: OrderRec) -> OrderRec:
        return order

    @app.handler
    def persist(order: OrderRec) -> bool:
        return True

    app.flow("order_pipeline", parse, validate, persist)
    return app


class T1Skeleton(unittest.TestCase):
    """Registration produces a graph; handlers + In/Out + connections
    are enumerable."""

    def test_graph_enumerable(self):
        app = build_app()
        g = app.graph
        self.assertEqual(g.namespace, "orders")
        self.assertEqual([h.name for h in g.handlers],
                         ["parse", "validate", "persist"])
        self.assertEqual(g.handler("parse").in_type.topo(), "str")
        self.assertEqual(g.handler("parse").out_type.topo(),
                         "record<id: int, amount: float>")
        self.assertEqual(g.handler("persist").out_type.topo(), "bool")
        self.assertIsNotNone(g.flow)
        self.assertEqual(len(g.flow.edges), 3)  # parse->validate->persist->void

    def test_source_handler_has_no_input(self):
        # A no-parameter handler is a legal source handler.
        app = topo.App("src")

        @app.handler
        def seed() -> int:
            return 0

        self.assertIsNone(app.graph.handler("seed").in_type)

    def test_handler_stays_independently_callable(self):
        # A handler is a plain fn after registration — independently
        # invocable and unit-testable with zero framework bootstrap.
        app = topo.App("x")

        @app.handler
        def double(n: int) -> int:
            return n * 2

        self.assertEqual(double(21), 42)  # no framework bootstrap needed


class T2Emit(unittest.TestCase):
    """Emitted .topo parses under the merged grammar."""

    def test_emitted_topo_parses(self):
        app = build_app()
        text = topo.config(app).emit_topo()
        self.assertIn("handler parse(str in) -> record<id: int, amount: float>;",
                      text)
        self.assertIn("flow order_pipeline {", text)
        # read_topo() raises if `topo` rejects the source — parsing it is
        # itself the grammar-conformance proof.
        from topo._readback import read_topo
        g2 = read_topo(text)
        self.assertEqual(g2.namespace, "orders")


class T3RoundTrip(unittest.TestCase):
    """graph -> .topo -> graph' with graph == graph' (headline)."""

    def test_semantic_equivalence(self):
        app = build_app()
        g1 = app.graph
        g2 = topo.config(app).roundtrip()
        self.assertTrue(g1.equivalent_to(g2),
                        f"{g1.semantic_key()} != {g2.semantic_key()}")

    def test_hand_edit_survives_readback(self):
        # The .topo is a view, not an opaque IR: reorder handlers/edges by
        # hand, read back, still semantically equivalent.
        app = build_app()
        text = topo.config(app).emit_topo()
        edited = text.replace(
            "      parse -> validate;\n      validate -> persist;",
            "      validate -> persist;\n      parse -> validate;",
        )
        from topo._readback import read_topo
        self.assertTrue(app.graph.equivalent_to(read_topo(edited)))


class T4ZeroDeclarationCheck(unittest.TestCase):
    """Zero hand-written .topo, the existing topo-check runs."""

    def _app_with_source(self, src_text):
        td = tempfile.mkdtemp()
        src = Path(td) / "app.py"
        src.write_text(src_text, "utf-8")
        ns = {}
        exec(compile(src_text, str(src), "exec"), ns)  # noqa: S102
        return ns["app"], str(src)

    COMPLIANT = (
        "import topo\n"
        "app = topo.App('orders')\n"
        "@app.handler\n"
        "def parse(raw: int) -> int:\n"
        "    return raw + 1\n"
        "@app.handler\n"
        "def enrich(v: int) -> int:\n"
        "    return v * 2\n"
        "@app.handler\n"
        "def audit(v: int) -> int:\n"
        "    return v\n"
        "@app.handler\n"
        "def total(v: int) -> float:\n"
        "    return float(v) + 0.5\n"
        "app.flow('pipeline', parse, topo.parallel(enrich, audit), total)\n"
    )

    VIOLATING = COMPLIANT.replace(
        "def audit(v: int) -> int:\n    return v\n",
        "def audit(v: int) -> int:\n"
        "    global _log\n"
        "    _log += v\n"
        "    return v\n",
    ).replace("app = topo.App('orders')\n",
              "app = topo.App('orders')\n_log = 0\n")

    def test_compliant_app_passes(self):
        app, src = self._app_with_source(self.COMPLIANT)
        r = topo.check(app, [src])
        self.assertTrue(r.passed, r.stdout + r.stderr)

    def test_violating_handler_is_flagged(self):
        # A flow handler with a hidden module-global write is a parallel
        # candidate at the same stage as a sibling handler; topo-check's
        # PurityCheck must flag it even though the source carries no
        # hand-written .topo.
        app, src = self._app_with_source(self.VIOLATING)
        r = topo.check(app, [src])
        self.assertFalse(r.passed,
                         "violating handler should be flagged by topo-check")


class T5ConfigEntry(unittest.TestCase):
    """topo.config(): snapshot lists full graph; emit_topo == emitter."""

    def test_snapshot_lists_full_graph(self):
        app = build_app()
        snap = topo.config(app).snapshot()
        self.assertEqual(snap["namespace"], "orders")
        self.assertEqual(len(snap["handlers"]), 3)
        self.assertEqual(snap["flow"]["name"], "order_pipeline")
        self.assertEqual(len(snap["flow"]["edges"]), 3)

    def test_config_emit_equals_emitter_output(self):
        app = build_app()
        from topo._emit import emit_topo as raw_emit
        self.assertEqual(topo.config(app).emit_topo(), raw_emit(app.graph))


if __name__ == "__main__":
    if "TOPO_BIN_DIR" not in os.environ:
        print("note: TOPO_BIN_DIR unset; relying on PATH or a sibling "
              "build tree", file=sys.stderr)
    unittest.main()
