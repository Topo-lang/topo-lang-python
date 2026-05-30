#!/usr/bin/env python3
"""Regression test: per-file syntax errors must not cause silent L2 coverage loss.

The L2 extractor (``topo_extract_python.py``) now emits a structured
``fileErrors`` array alongside ``callSites`` so the C++ analyzer can
surface per-file coverage loss as distinct warnings. Pre-fix, a per-file
``SyntaxError`` was reported only via stderr and the analyzer forwarded
that combined blob as a single warning — no per-file accounting and no
"K of N files analysed" summary.

Run: ``python3 topo-lang-python/topo-check/extractor/test/test_file_errors.py``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "topo_extract_python.py")


def _run(*paths: str) -> dict:
    res = subprocess.run(
        [sys.executable, SCRIPT, *paths],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    if res.returncode != 0:
        raise AssertionError(
            f"extractor exited {res.returncode}: {res.stderr}")
    return json.loads(res.stdout)


class FileErrorsContract(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="topo-extract-py-fileerrors-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name: str, body: str) -> str:
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_emits_empty_fileerrors_when_all_files_parse(self):
        a = self._write("good.py", "import os\nos.system('ls')\n")
        resp = _run(a)
        self.assertIn("callSites", resp)
        self.assertIn("fileErrors", resp)
        self.assertEqual(resp["fileErrors"], [])
        # Sanity: os.system call site present.
        self.assertTrue(any(s["callee"] == "os.system"
                            for s in resp["callSites"]))

    def test_syntax_error_recorded_per_file(self):
        good = self._write("good.py", "import os\nos.system('ls')\n")
        bad = self._write("bad.py", "def f(:\n    pass\n")  # malformed
        resp = _run(good, bad)
        # Good file's call site survives — bad file's parse failure does
        # not pollute it.
        self.assertTrue(any(s["callee"] == "os.system"
                            for s in resp["callSites"]))
        # Structured per-file error entry.
        errs = resp["fileErrors"]
        self.assertEqual(len(errs), 1, errs)
        e = errs[0]
        self.assertEqual(e["file"], bad)
        self.assertEqual(e["kind"], "syntax-error")
        self.assertGreaterEqual(e["line"], 1)
        self.assertTrue(e["message"])

    def test_read_error_recorded_with_read_error_kind(self):
        missing = os.path.join(self.dir, "does-not-exist.py")
        resp = _run(missing)
        errs = resp["fileErrors"]
        self.assertEqual(len(errs), 1, errs)
        self.assertEqual(errs[0]["kind"], "read-error")
        self.assertEqual(errs[0]["file"], missing)


if __name__ == "__main__":
    unittest.main()
