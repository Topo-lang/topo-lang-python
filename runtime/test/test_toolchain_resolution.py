"""Unit tests for ``topo._toolchain`` binary resolution — no real
toolchain needed: fake ``topo`` binaries are staged in temp dirs and
resolved through each tier (strict TOPO_BIN_DIR, PATH, fixed-order
sibling build trees). Mirrors the typescript runtime's
test_toolchain_resolution.test.mjs lanes so the canonical order stays
pinned in every locator; the Windows PATHEXT story is covered by
monkey-patching ``is_windows`` (same approach as test_platform.py).

Run: ``python3 -m unittest test_toolchain_resolution`` (from this dir).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topo import _platform  # noqa: E402
from topo import _toolchain  # noqa: E402

_TOPO_REL = Path("topo-core") / "tools" / "topo" / "topo"


def _stage_fake_topo(base: Path, rel) -> Path:
    """A regular file with the execute bit is all resolution stats for."""
    if _platform.is_windows():
        # is_executable on Windows requires a PATHEXT-matching suffix —
        # stage a dummy .exe next to the bare name (the resolver probes
        # the .exe sibling when the bare name is absent).
        p = base / (str(rel) + ".exe")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MZ")
        return p
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


class ToolchainResolutionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # A guaranteed-empty PATH entry so tiers under test stay isolated
        # from the host's real PATH (which may carry an installed topo).
        self.empty_dir = self.tmp / "empty"
        self.empty_dir.mkdir()

    def _env(self, **overrides):
        """Controlled environ: TOPO_BIN_DIR dropped unless overridden."""
        env = {k: v for k, v in os.environ.items() if k != "TOPO_BIN_DIR"}
        env.update(overrides)
        return mock.patch.dict(os.environ, env, clear=True)

    # ── Tier 1: TOPO_BIN_DIR (strict) ─────────────────────

    def test_topo_bin_dir_flat_layout_resolves(self):
        staged = _stage_fake_topo(self.tmp, "topo")
        with self._env(TOPO_BIN_DIR=str(self.tmp), PATH=str(self.empty_dir)):
            self.assertEqual(_toolchain.topo_bin(), staged)

    def test_topo_bin_dir_nested_layout_resolves(self):
        staged = _stage_fake_topo(self.tmp, _TOPO_REL)
        with self._env(TOPO_BIN_DIR=str(self.tmp), PATH=str(self.empty_dir)):
            self.assertEqual(_toolchain.topo_bin(), staged)

    def test_topo_bin_dir_multi_config_subdir_resolves(self):
        staged = _stage_fake_topo(self.tmp, Path("Release") / "topo")
        with self._env(TOPO_BIN_DIR=str(self.tmp), PATH=str(self.empty_dir)):
            self.assertEqual(_toolchain.topo_bin(), staged)

    def test_topo_bin_dir_set_but_unresolvable_raises_even_with_path(self):
        # STRICT override: a resolvable PATH must NOT rescue a stale
        # TOPO_BIN_DIR — that would silently swap the binary under test.
        path_dir = self.tmp / "onpath"
        _stage_fake_topo(path_dir, "topo")
        with self._env(TOPO_BIN_DIR=str(self.empty_dir), PATH=str(path_dir)):
            with self.assertRaises(FileNotFoundError) as ctx:
                _toolchain.topo_bin()
        self.assertIn("TOPO_BIN_DIR", str(ctx.exception))

    # ── Tier 2: PATH ──────────────────────────────────────

    def test_path_tier_resolves_bare_name(self):
        path_dir = self.tmp / "onpath"
        staged = _stage_fake_topo(path_dir, "topo")
        with self._env(PATH=str(path_dir)):
            self.assertEqual(_toolchain.topo_bin(), staged)

    # ── Tier 3: sibling build trees (fixed order) ─────────

    def test_build_tree_resolves_via_repo_root(self):
        staged = _stage_fake_topo(self.tmp / "build", _TOPO_REL)
        with self._env(PATH=str(self.empty_dir)), \
             mock.patch.object(_toolchain, "_REPO_ROOT", self.tmp):
            self.assertEqual(_toolchain.topo_bin(), staged)

    def test_build_no_llvm_tree_is_excluded(self):
        # The distrusted tree must never resolve, even when it is the only
        # one present (exclusion pinned — the locator raises instead).
        _stage_fake_topo(self.tmp / "build-no-llvm", _TOPO_REL)
        with self._env(PATH=str(self.empty_dir)), \
             mock.patch.object(_toolchain, "_REPO_ROOT", self.tmp):
            with self.assertRaises(FileNotFoundError):
                _toolchain.topo_bin()

    def test_build_preferred_over_build_asan_fixed_order(self):
        build_hit = _stage_fake_topo(self.tmp / "build", _TOPO_REL)
        asan_hit = _stage_fake_topo(self.tmp / "build-asan", _TOPO_REL)
        # Make the asan binary strictly newer: fixed order must win over
        # any recency heuristic.
        t = build_hit.stat().st_mtime
        os.utime(asan_hit, (t + 100, t + 100))
        with self._env(PATH=str(self.empty_dir)), \
             mock.patch.object(_toolchain, "_REPO_ROOT", self.tmp):
            self.assertEqual(_toolchain.topo_bin(), build_hit)

    # ── Windows lanes (simulated, as in test_platform.py) ─

    def test_windows_path_tier_resolves_exe_via_pathext(self):
        path_dir = self.tmp / "onpath"
        path_dir.mkdir()
        exe = path_dir / "topo.exe"
        exe.write_bytes(b"MZ")
        with self._env(PATH=str(path_dir)), \
             mock.patch.object(_platform, "is_windows", return_value=True), \
             mock.patch.object(_toolchain, "is_windows", return_value=True):
            self.assertEqual(_toolchain.topo_bin(), exe)

    def test_windows_non_pathext_file_is_not_runnable(self):
        # An extensionless regular file is not PATHEXT-runnable, so the
        # strict env tier must reject it and raise.
        bad_dir = self.tmp / "noext"
        bad_dir.mkdir()
        (bad_dir / "topo").write_text("not runnable")
        with self._env(TOPO_BIN_DIR=str(bad_dir), PATH=str(self.empty_dir)), \
             mock.patch.object(_platform, "is_windows", return_value=True), \
             mock.patch.object(_toolchain, "is_windows", return_value=True):
            with self.assertRaises(FileNotFoundError):
                _toolchain.topo_bin()


if __name__ == "__main__":
    unittest.main()
