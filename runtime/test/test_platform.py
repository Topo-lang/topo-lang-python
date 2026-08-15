"""Unit tests for ``topo._platform`` and the related ``_toolchain``
resolution changes that motivate the helper.

The helpers must work on Windows, macOS, and Linux. We can run the
suite on POSIX hosts only; the Windows-specific PATHEXT logic is covered
by monkey-patching ``topo._platform.is_windows`` so we get coverage of
both code paths from one machine.

Run: ``python3 -m unittest topo-lang-python.runtime.test.test_platform``
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topo import _platform  # noqa: E402
from topo import _toolchain  # noqa: E402


class IsExecutablePOSIX(unittest.TestCase):
    """POSIX semantics: regular file with the execute bit set."""

    def test_executable_file_is_runnable(self):
        # sys.executable is always a runnable interpreter on the host
        # we're running on, so it satisfies the contract by construction.
        self.assertTrue(_platform.is_executable(sys.executable))

    def test_nonexistent_path_is_not_executable(self):
        self.assertFalse(_platform.is_executable("/no/such/binary"))

    def test_directory_is_not_executable(self):
        # A directory has the X bit set on POSIX (so you can cd into it)
        # but is_executable explicitly requires a regular file.
        self.assertFalse(_platform.is_executable(os.path.dirname(sys.executable)))


class IsExecutableWindowsSimulated(unittest.TestCase):
    """Windows semantics: PATHEXT-matching suffix, no X_OK probe.

    We simulate Windows by patching ``is_windows`` so the same physical
    host exercises the Windows code path. The PATHEXT default mirrors
    Microsoft's documented default value.
    """

    def setUp(self):
        # A real file we can probe (the test file itself).
        self.path = Path(__file__)

    def test_pathext_match_is_executable(self):
        # On simulated Windows, a file ending in .EXE is executable
        # regardless of POSIX bits. We point is_executable at the test
        # file with a fake .exe-suffix path.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            tmp = Path(f.name)
        try:
            with mock.patch.object(_platform, "is_windows", return_value=True):
                self.assertTrue(_platform.is_executable(tmp))
        finally:
            tmp.unlink(missing_ok=True)

    def test_non_pathext_suffix_is_not_executable_on_windows(self):
        # The test file ends in ``.py``; under default PATHEXT (.COM
        # .EXE .BAT .CMD .VBS .JS .WS .MSC) it is NOT runnable.
        with mock.patch.object(_platform, "is_windows", return_value=True):
            self.assertFalse(_platform.is_executable(self.path))


class FindPythonInterpreter(unittest.TestCase):
    def test_returns_current_interpreter_by_default(self):
        # In a typical test invocation there is no TOPO_PYTHON set, so
        # the helper falls back to sys.executable. The first element of
        # the returned argv must be a runnable interpreter.
        env = dict(os.environ)
        env.pop("TOPO_PYTHON", None)
        env.pop("TOPO_PYTHON_EXE", None)
        with mock.patch.dict(os.environ, env, clear=True):
            argv = _platform.find_python_interpreter()
        self.assertGreaterEqual(len(argv), 1)
        self.assertTrue(_platform.is_executable(argv[0]))

    def test_topo_python_env_override_wins(self):
        with mock.patch.dict(os.environ, {"TOPO_PYTHON": sys.executable}):
            argv = _platform.find_python_interpreter()
        self.assertEqual(argv, [sys.executable])

    def test_topo_python_invalid_falls_back_to_default(self):
        # An unresolvable TOPO_PYTHON value should not crash — the
        # helper falls through to the standard discovery chain. This is
        # important for users who set TOPO_PYTHON in their shell rc but
        # then move/uninstall the interpreter.
        env = dict(os.environ)
        env["TOPO_PYTHON"] = "/nonexistent/python-binary"
        with mock.patch.dict(os.environ, env, clear=True):
            argv = _platform.find_python_interpreter()
        self.assertTrue(_platform.is_executable(argv[0]))


class ToolchainResolvesViaPlatformHelper(unittest.TestCase):
    """``_toolchain`` must route its X-bit / suffix decision through
    ``_platform.is_executable`` — exercising _resolve_in proves the
    integration end to end (POSIX path)."""

    def test_resolve_in_finds_real_executable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            if _platform.is_windows():
                # is_executable on Windows requires a PATHEXT-matching
                # suffix — the .exe-sibling probe is this test's Windows
                # shape (the mocked variant is
                # test_resolve_in_handles_windows_exe_suffix; this one runs
                # against the real platform helper).
                target = tdp / "topo-core" / "tools" / "topo" / "topo.exe"
                target.parent.mkdir(parents=True)
                target.write_bytes(b"MZ")
            else:
                target = tdp / "topo-core" / "tools" / "topo" / "topo"
                target.parent.mkdir(parents=True)
                target.write_text("#!/bin/sh\nexit 0\n")
                target.chmod(0o755)
            hit = _toolchain._resolve_in(tdp, "topo-core/tools/topo/topo")
            self.assertEqual(hit, target)

    def test_resolve_in_handles_windows_exe_suffix(self):
        # On simulated Windows, a .exe sibling next to a missing bare
        # binary is what _resolve_in must find. is_executable accepts
        # the .exe suffix even without the POSIX X bit.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            nested = tdp / "topo-core" / "tools" / "topo"
            nested.mkdir(parents=True)
            exe = nested / "topo.exe"
            exe.write_bytes(b"MZ")
            with mock.patch.object(_platform, "is_windows", return_value=True), \
                 mock.patch.object(_toolchain, "is_windows", return_value=True):
                hit = _toolchain._resolve_in(tdp, "topo-core/tools/topo/topo")
            self.assertEqual(hit, exe)


if __name__ == "__main__":
    unittest.main()
