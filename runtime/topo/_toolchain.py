"""Locate the built Topo toolchain binaries.

topo-app is a product layer that *consumes* the existing toolchain; it
never reimplements parsing or checking. The binaries are produced by the
project's CMake build. Resolution order (canonical across the python /
typescript / java runtimes):

  1. explicit env var (TOPO_BIN_DIR) — the CI/test pinning contract; may
     point at a build tree or straight at a bin directory. STRICT: when
     set, the binary must resolve under it or the locator raises with an
     actionable error. Falling through to PATH would silently swap the
     binary under test — exactly the "silently degrading a correctness
     tool" failure mode this module exists to prevent.
  2. PATH lookup for the bare binary name — the layout ``cmake
     --install``, Homebrew, and the per-package installs ship into, and
     the only resolution that works outside a source checkout.
     PATHEXT-aware on Windows.
  3. known sibling build trees of this checkout, fixed order, first hit
     wins: ``build/`` then ``build-asan/``. ``build-no-llvm/`` is
     deliberately NOT searched: a stale tree predating current grammar
     support mis-resolves and produces spurious parse failures. Only
     freshly built trees are trusted; pin TOPO_BIN_DIR to override the
     fixed order.

A clear error is raised if no tier yields the binary, because silently
degrading a correctness tool would defeat the point.

Windows portability: ``is_executable`` (from _platform) recognises
PATHEXT-matching files (the X_OK probe is unreliable on Windows for
``.exe`` files), and _candidate_paths_for transparently probes the
``.exe`` suffix + multi-config subdirs (Release/, Debug/) emitted by
Visual Studio / Xcode generators.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from ._platform import _pathext, is_executable, is_windows

# This file lives at topo-lang-python/runtime/topo/_toolchain.py; the
# repository root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Sibling build trees probed by tier 3, in priority order (first hit
# wins). build-no-llvm/ is deliberately excluded — see the module
# docstring.
_BUILD_DIRS = ("build", "build-asan")

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


def _find_on_path(bare: str) -> Optional[Path]:
    """Cross-platform ``which``: walk PATH for the bare binary name,
    honouring PATHEXT on Windows so a request for "topo" correctly finds
    "topo.exe". Hand-rolled (not shutil.which) so the PATHEXT semantics
    stay identical to the typescript / java locators. Returns None if
    not found.
    """
    path_env = os.environ.get("PATH")
    if not path_env:
        return None
    suffixes: List[str] = [""]
    if is_windows():
        suffixes.extend(_pathext())
    for d in path_env.split(os.pathsep):
        if not d:
            continue
        for sfx in suffixes:
            cand = Path(d) / (bare + sfx)
            if is_executable(cand):
                return cand
    return None


def _find(rel: str) -> Path:
    # 1. Explicit override — STRICT: set means "use exactly this tree".
    env = os.environ.get("TOPO_BIN_DIR")
    if env:
        hit = _resolve_in(Path(env), rel)
        if hit is not None:
            return hit
        raise FileNotFoundError(
            f"TOPO_BIN_DIR={env!r} is set but does not contain an "
            f"executable '{rel}'. Point TOPO_BIN_DIR at a build tree "
            f"that has been built (cmake --build <tree> --target "
            f"topo topo-check), or unset it to fall back to PATH / the "
            f"sibling build trees."
        )

    # 2. PATH probe — the installed-package layout.
    on_path = _find_on_path(Path(rel).name)
    if on_path is not None:
        return on_path

    # 3. Sibling build trees, fixed order, first hit wins (see the
    #    module docstring for why build-no-llvm/ is excluded).
    for b in _BUILD_DIRS:
        hit = _resolve_in(_REPO_ROOT / b, rel)
        if hit is not None:
            return hit

    raise FileNotFoundError(
        f"could not locate '{rel}'. Install the Topo toolchain (so it is "
        f"on PATH), build it (cmake --build build --target topo "
        f"topo-check), or set TOPO_BIN_DIR (must be a current build tree, "
        f"not the stale build-no-llvm)."
    )


def topo_bin() -> Path:
    return _find("topo-core/tools/topo/topo")


def topo_check_bin() -> Path:
    return _find("topo-cli/tools/topo-check/topo-check")
