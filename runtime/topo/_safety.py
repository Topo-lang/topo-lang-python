"""Boundary-input sanitisation helpers for topo-app's Python runtime.

Companion to ``_platform.py`` (which abstracts environment / interpreter
discovery). The helpers here defend the *other* class of cross-boundary
hazard: paths read from manifests / CLI flags / JSON stdin, and
identifiers fed into target-side eval. They are the Python projection of
the C++ ``topo::platform`` Sanitize.h API and follow the same project
contract: every value crossing a system boundary (CLI flag, JSON
payload, env var, file path read from untrusted source) is validated
once at the boundary; downstream code can then assume the value is
sanitised.

Stdlib-only on purpose: importing this module must not pull in any
third-party package. The runtime sanitises before any optional
dependency is touched, just like ``_platform.py``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union

# Conservative identifier regex: matches the same character class as
# Python's own identifier grammar (ASCII subset). Used for ``--var``
# argument validation in topo-debug-python; the pdb fallback otherwise
# interpolates the bare string into an eval template and would happily
# execute ``__import__('os').system(...)`` payloads.
_SAFE_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sanitize_path(s: Union[str, os.PathLike],
                  root: Union[str, os.PathLike]) -> Optional[Path]:
    """Canonicalise ``s`` and confirm it stays under ``root``.

    Returns the resolved ``Path`` on success and ``None`` on any reject
    condition (path-traversal segment, absolute path escaping root,
    symlink crossing the root boundary, empty input). The caller MUST
    treat ``None`` as a hard reject — never fall back to the verbatim
    string.

    Symlinks are resolved via ``Path.resolve(strict=False)`` so a not-
    yet-created destination still has the on-disk prefix follow
    symlinks; the containment check then operates on the resolved form.
    """
    if not s or not root:
        return None

    raw = Path(os.fspath(s))
    root_path = Path(os.fspath(root))

    try:
        root_resolved = root_path.resolve(strict=False)
    except OSError:
        return None

    candidate = raw if raw.is_absolute() else (root_resolved / raw)

    # Reject any residual ``..`` lexically before touching the
    # filesystem. ``foo/../../etc/passwd`` normalises to ``/etc/passwd``;
    # the subpath check below would catch it, but the lexical reject
    # short-circuits the symlink-follow path entirely.
    if ".." in candidate.parts:
        # Allow ``..`` only if it disappears after normalisation under
        # the root prefix; check by recomputing.
        normalised = Path(os.path.normpath(str(candidate)))
        if ".." in normalised.parts:
            return None
        candidate = normalised

    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None

    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def safe_var_name(s: str) -> bool:
    """True iff ``s`` is a valid Python identifier (ASCII subset).

    Used at every CLI/eval boundary in topo-debug-python: anything sent
    through a probe template that the target process eventually
    evaluates must be confirmed by this gate first. Returns ``False``
    for ``None``, empty string, or any string containing non-identifier
    characters. Reserved keywords are NOT rejected — the gate is a
    syntactic check; the target process's NameError handling is the
    semantic backstop.
    """
    if not isinstance(s, str):
        return False
    if not s:
        return False
    return bool(_SAFE_VAR_NAME_RE.match(s))
