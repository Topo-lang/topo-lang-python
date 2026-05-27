"""Platform-aware helpers for topo-app's Python runtime.

The runtime resolves binaries by walking the local build tree(s) and
also shells out to a Python interpreter for the L2 ast extractor. Both
paths must work on Windows / macOS / Linux without a host-side fork —
``os.access(p, os.X_OK)`` is documented as unreliable on Windows
(``.exe`` files inherit no POSIX execute bit), and the literal name
``python3`` is not on the default Windows PATH at all (Microsoft ships
``python.exe`` and the ``py`` launcher only).

This module collects the cross-platform helpers in one place so the
rest of the runtime treats "is this runnable?" and "where is python?"
as plain functions, never bespoke probes scattered across modules.

Stdlib-only on purpose: importing this from ``_toolchain`` must not
trigger any third-party module load (we resolve binaries before any
optional dependency is touched).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Windows users override the interpreter discovery with this; honoured
# first on every platform so CI / containers can pin a specific binary.
_INTERPRETER_ENV_VARS: Tuple[str, ...] = ("TOPO_PYTHON", "TOPO_PYTHON_EXE")

# Candidate interpreter names probed in order on auto-discovery.
# On POSIX systems ``python3`` is conventional; on Windows the canonical
# names are ``python.exe`` and the version-aware launcher ``py.exe``
# (which we drive via ``py -3`` to request a Python 3 interpreter).
_POSIX_CANDIDATES: Tuple[str, ...] = ("python3", "python")
_WINDOWS_CANDIDATES: Tuple[str, ...] = ("python.exe", "python", "python3.exe", "python3")


def is_windows() -> bool:
    return os.name == "nt"


def _pathext() -> List[str]:
    """The Windows-style executable suffixes that PATHEXT advertises.

    On POSIX systems we return ``[""]`` so the suffix check is a no-op
    (any regular file with the execute bit is considered runnable). On
    Windows the runnable extensions come from ``PATHEXT``; the default
    value is ``".COM;.EXE;.BAT;.CMD"``.
    """
    if not is_windows():
        return [""]
    raw = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC")
    # PATHEXT uses ``;`` as separator on Windows regardless of
    # ``os.pathsep`` (which would be ``:`` if we somehow ran this on
    # a POSIX host under a Windows simulation).
    return [s.strip().lower() for s in raw.split(";") if s.strip()]


def is_executable(path) -> bool:
    """Cross-platform "is this file runnable?" check.

    On POSIX the answer is the standard ``os.access(.., os.X_OK)`` check
    applied to a regular file. On Windows we ignore the X_OK probe —
    which the stdlib docs warn is unreliable for ``.exe`` files — and
    instead require the file's suffix to match an entry in PATHEXT (the
    same rule cmd.exe uses to decide what to launch).
    """
    p = Path(path)
    try:
        if not p.is_file():
            return False
    except OSError:
        return False

    if is_windows():
        suffix = p.suffix.lower()
        return suffix in _pathext()
    return os.access(p, os.X_OK)


def _candidate_names() -> Iterable[str]:
    return _WINDOWS_CANDIDATES if is_windows() else _POSIX_CANDIDATES


def find_python_interpreter() -> List[str]:
    """Locate a Python 3 interpreter as an argv prefix.

    Returns the argv prefix (e.g. ``["python3"]`` or ``["py", "-3"]``)
    callers should prepend to script arguments. Raising is intentional:
    a missing interpreter is an actionable user error, not a silent
    fallback, so the call site can emit a clear "set TOPO_PYTHON to the
    interpreter path" diagnostic rather than a generic
    ``"exit code 1"`` later.

    Resolution order:

      1. ``TOPO_PYTHON`` / ``TOPO_PYTHON_EXE`` env var (explicit
         override, used by tests / CI / nonstandard installs).
      2. The current process's own interpreter (``sys.executable``) —
         the most reliable choice when the runtime itself is running
         under a working Python.
      3. ``shutil.which()`` over the platform-appropriate candidate
         names (``python3``/``python`` on POSIX;
         ``python.exe``/``python`` on Windows).
      4. ``shutil.which("py")`` + ``["-3"]`` (the Windows launcher;
         honoured on POSIX too if it happens to be installed).
    """
    for env in _INTERPRETER_ENV_VARS:
        v = os.environ.get(env)
        if v and is_executable(v):
            return [v]
        # Permit a bare command name as the override (e.g.
        # TOPO_PYTHON=python3.11 without a path); fall through to
        # which() if so.
        if v:
            resolved = shutil.which(v)
            if resolved is not None:
                return [resolved]

    # The current interpreter is always runnable — it's already running
    # this code. Prefer it before probing PATH so a venv-isolated runtime
    # does not accidentally shell out to a different system Python.
    if sys.executable and is_executable(sys.executable):
        return [sys.executable]

    for name in _candidate_names():
        resolved = shutil.which(name)
        if resolved is not None:
            return [resolved]

    # Windows fallback: the py launcher dispatches to whatever Python
    # the user has installed. ``py -3`` requests any Python 3.
    py = shutil.which("py.exe") or shutil.which("py")
    if py is not None:
        return [py, "-3"]

    raise FileNotFoundError(
        "No Python interpreter found on PATH. "
        "Set TOPO_PYTHON to the interpreter path "
        "(e.g. TOPO_PYTHON=python3, TOPO_PYTHON=C:\\Python311\\python.exe), "
        "install Python 3, or run from a venv that has it activated."
    )


def find_python_interpreter_or_none() -> Optional[List[str]]:
    """Best-effort variant that returns None instead of raising."""
    try:
        return find_python_interpreter()
    except FileNotFoundError:
        return None
