"""Zero-declaration check: hand the existing topo-check the emitted .topo.

The framework gives the user a free ``topo check`` — they write no
``.topo`` by hand. We materialise a throwaway project (Topo.toml +
emitted .topo + the user's Python sources), run the *existing*
topo-check binary against it, and surface the verdict. No checking logic
is reimplemented here; this is pure orchestration.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

from ._emit import emit_topo
from ._toolchain import topo_check_bin
from .app import App

_TOPO_TOML = """\
[project]
name = "{name}"

[topo]
root = "topo/app.topo"

[build]
language = "python"
sources = ["src/*.py"]

[purity]
mode = "force"

[completeness]
ignore_constructors = true
ignore_main = true
"""


@dataclass
class CheckResult:
    passed: bool
    returncode: int
    stdout: str
    stderr: str


def check(app: App, python_sources: List[str]) -> CheckResult:
    """Run topo-check on the framework-emitted .topo against the given
    Python source files. No hand-written .topo anywhere in the flow."""

    name = app.graph.namespace or "topo_app"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "topo").mkdir()
        (root / "src").mkdir()
        (root / "Topo.toml").write_text(_TOPO_TOML.format(name=name), "utf-8")
        (root / "topo" / "app.topo").write_text(emit_topo(app.graph), "utf-8")
        for srcfile in python_sources:
            shutil.copy(srcfile, root / "src" / Path(srcfile).name)

        proc = subprocess.run(
            [str(topo_check_bin()), "--project", str(root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    # topo-check exits 0 on PASS; the textual verdict is the source of
    # truth (exit codes are not always non-zero on logical FAIL).
    passed = "Result: PASS" in proc.stdout
    return CheckResult(
        passed=passed,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
