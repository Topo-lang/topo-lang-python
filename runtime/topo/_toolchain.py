"""Locate the built Topo toolchain binaries.

topo-app is a product layer that *consumes* the existing toolchain; it
never reimplements parsing or checking. The binaries are produced by the
project's CMake build. Resolution order:

  1. explicit env var (TOPO_BIN_DIR) — used by tests and CI
  2. a sibling build tree of this checkout

A clear error is raised if neither yields the binary, because silently
degrading a correctness tool would defeat the point.

Newest-tree-wins (within the sibling build dirs): the historical bug was
that ``_BUILD_DIRS`` is an *ordered* list and ``_find`` returned the
first directory that merely *existed*. A month-stale ``build-no-llvm/``
binary (predating current grammar/parser support) then silently shadowed
a freshly built ``build/``, producing spurious failures that looked like
product bugs. There is no robust way to map a binary's mtime to a
grammar version, so the deterministic, defensible heuristic is: among the
candidate sibling trees, pick the executable with the newest mtime. An
explicit ``TOPO_BIN_DIR`` always wins outright (it is the CI/test
contract); auto-resolution across the unpinned build dirs only kicks in
when no override is given, and there it prefers the most-recently-built
tree rather than a fixed directory order.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from ._platform import is_executable, is_windows

# This file lives at topo-lang-python/runtime/topo/_toolchain.py; the
# repository root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Order here is NOT a priority order any more — see the module docstring.
# It is only the set of sibling build trees to consider when no explicit
# TOPO_BIN_DIR is given; the newest-mtime executable among them wins.
#
# Windows portability: ``is_executable`` (from _platform) recognises
# PATHEXT-matching files (the X_OK probe is unreliable on Windows for
# ``.exe`` files), and _candidate_paths_for transparently probes the
# ``.exe`` suffix + multi-config subdirs (Release/, Debug/) emitted by
# Visual Studio / Xcode generators. A freshly built Windows toolchain
# is discoverable without a special path.
_BUILD_DIRS = ("build-no-llvm", "build", "build-asan")

# Per-build sub-config directories CMake's multi-config generators emit
# (Visual Studio, Xcode). Probed after the flat layout.
_CONFIG_SUBDIRS = ("Release", "RelWithDebInfo", "Debug")


def _candidate_paths_for(base: Path, rel: str) -> List[Path]:
    """All on-disk paths where the binary ``rel`` may live under ``base``."""
    name = Path(rel).name
    suffixes: List[str] = [""]
    if is_windows():
        suffixes.append(".exe")

    rel_path = Path(rel)
    candidates: List[Path] = []
    for sfx in suffixes:
        # Nested layout (per-tool subdirectory, source-tree mirror).
        candidates.append(base / (rel + sfx))
        # Flat bin layout (Homebrew / cmake --install / topo backend).
        candidates.append(base / (name + sfx))
        # Multi-config layouts (Visual Studio / Xcode generators).
        for cfg in _CONFIG_SUBDIRS:
            candidates.append(base / cfg / (rel + sfx))
            candidates.append(base / cfg / (name + sfx))
            if len(rel_path.parts) > 1:
                candidates.append(base / rel_path.parent / cfg / (rel_path.name + sfx))
    return candidates


def _resolve_in(base: Path, rel: str) -> Optional[Path]:
    """Return the executable for ``rel`` under ``base``, or None.

    ``base`` may be a full build tree (then ``base/rel``) or point
    straight at a bin directory (then ``base/<basename>``). Windows
    ``.exe`` suffix and multi-config subdirs are probed automatically.
    """
    for cand in _candidate_paths_for(base, rel):
        if is_executable(cand):
            return cand
    return None


def _find(rel: str) -> Path:
    # 1. Explicit override always wins outright — the CI/test contract.
    env = os.environ.get("TOPO_BIN_DIR")
    if env:
        hit = _resolve_in(Path(env), rel)
        if hit is not None:
            return hit
        raise FileNotFoundError(
            f"TOPO_BIN_DIR={env!r} is set but does not contain an "
            f"executable '{rel}'. Point TOPO_BIN_DIR at a build tree "
            f"that has been built (cmake --build <tree> --target "
            f"topo topo-check)."
        )

    # 2. No override: pick the NEWEST-built executable across the
    #    candidate sibling trees. Resolution is deterministic — ties on
    #    mtime fall back to _BUILD_DIRS order — so a stale build-no-llvm/
    #    can no longer silently shadow a fresh build/.
    best: Optional[Path] = None
    best_mtime = -1.0
    for b in _BUILD_DIRS:
        hit = _resolve_in(_REPO_ROOT / b, rel)
        if hit is None:
            continue
        mtime = hit.stat().st_mtime
        if mtime > best_mtime:
            best, best_mtime = hit, mtime
    if best is not None:
        return best

    raise FileNotFoundError(
        f"could not locate '{rel}'. Build the toolchain "
        f"(cmake --preset no-llvm && cmake --build build-no-llvm --target "
        f"topo topo-check) or set TOPO_BIN_DIR."
    )


def topo_bin() -> Path:
    return _find("topo-core/tools/topo/topo")


def topo_check_bin() -> Path:
    return _find("topo-cli/tools/topo-check/topo-check")
