"""The single unified configuration entry — the whole-graph view plus
the round-trip constraint as one object.

``snapshot()`` and ``emit_topo()`` are two views of the *same* logic
structure: the snapshot is the human/agent overview, the .topo is the
toolchain-consumable contract. They are kept consistent by construction
because both derive from the same Graph — two views of one logical
structure, never two artefacts to keep in sync by hand.
"""

from __future__ import annotations

import tomllib
from datetime import date, datetime, time
from typing import Any, Dict, Optional

from ._config_model import (
    BrowseEntry,
    ConfigStore,
    DevInternalRegistry,
    ItemPolicy,
    LayeredConfig,
)
from ._emit import emit_topo as _emit
from ._graph import Graph
from ._readback import read_topo
from .app import App


class Config:
    def __init__(self, app: App):
        self._app = app

    @property
    def graph(self) -> Graph:
        return self._app.graph

    def snapshot(self) -> dict:
        """The full graph: every handler with In/Out, every connection.
        One place, the whole picture."""

        g = self._app.graph
        return {
            "namespace": g.namespace,
            "handlers": [
                {
                    "name": h.name,
                    "in": None if h.in_type is None else h.in_type.topo(),
                    "out": h.out_type.topo(),
                }
                for h in g.handlers
            ],
            "flow": None
            if g.flow is None
            else {
                "name": g.flow.name,
                "edges": [
                    {"from": e.source, "to": "void" if e.is_terminal else e.target}
                    for e in g.flow.edges
                ],
            },
        }

    def emit_topo(self, path: Optional[str] = None) -> str:
        """The round-trippable .topo view of the same structure."""

        text = _emit(self._app.graph)
        if path is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return text

    def roundtrip(self) -> Graph:
        """Emit then read back through the real parser. Returns graph'."""

        return read_topo(self.emit_topo())


def config(app: App) -> Config:
    """The one ``topo.config(app)`` entry the topo-app surface names."""

    return Config(app)


# --- Python TOML bridge for the product runtime config ------------------
#
# The layered model is language-agnostic and never touches files. This is
# the Python ecosystem's bridge: it decodes ``topo-app.toml`` with the
# stdlib ``tomllib`` parser and serialises writes back. ``tomllib`` is
# read-only and the ecosystem's writer (``tomli-w``) is an optional
# third-party package; rather than add a hard runtime dependency the
# bridge ships a minimal *deterministic* TOML writer (sorted keys, stable
# table nesting) good enough for the flat scalar/array/table config
# vocabulary the model accepts. If a richer writer is ever needed the
# minimal one can be swapped without the model noticing.


def _split_nested(flat: Dict[str, Any]) -> Dict[str, Any]:
    """Turn dotted keys (``a.b.c``) into nested dict structure so the
    serialised TOML uses idiomatic ``[a.b]`` tables instead of quoted
    dotted keys."""

    root: Dict[str, Any] = {}
    for dotted in sorted(flat):
        parts = dotted.split(".")
        cursor = root
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = flat[dotted]
    return root


def _flatten_nested(nested: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Inverse of :func:`_split_nested`: a decoded TOML document back to
    the model's flat dotted-key map. A ``dict`` is treated as a nested
    table; a value the model stores as a ``record`` (also a ``dict``)
    only appears as a *value*, never recursed into here, because the
    config vocabulary keys are addressed by dotted path, and a stored
    table value is itself a leaf in that addressing."""

    flat: Dict[str, Any] = {}
    for name, value in nested.items():
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            flat.update(_flatten_nested(value, key))
        else:
            flat[key] = value
    return flat


def _toml_scalar(value: Any) -> str:
    """Serialise one scalar/array to TOML text, deterministically.

    Datetime types are intentionally *not* handled — the model rejects
    them before a write reaches the bridge, so reaching this branch would
    be a contract violation worth surfacing loudly."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    if isinstance(value, (datetime, date, time)):
        raise TypeError(
            "datetime values have no stdlib bridge and must be rejected "
            "by the model before serialisation"
        )
    raise TypeError(f"value of type {type(value).__name__} is not TOML-serialisable")


def _emit_toml(nested: Dict[str, Any], path: tuple = ()) -> str:
    """A minimal deterministic TOML emitter for the config vocabulary.

    Scalars/arrays of a table are written before nested sub-tables, keys
    are sorted, and a stored table *value* (a record) is written inline
    so it round-trips as one value rather than a sub-section."""

    scalars = {}
    subtables = {}
    for name in sorted(nested):
        value = nested[name]
        if isinstance(value, dict) and not _is_record_value(value):
            subtables[name] = value
        else:
            scalars[name] = value

    out = []
    for name in sorted(scalars):
        out.append(f"{name} = {_toml_value(scalars[name])}")
    for name in sorted(subtables):
        section = ".".join(path + (name,))
        body = _emit_toml(subtables[name], path + (name,))
        out.append(f"\n[{section}]")
        if body:
            out.append(body)
    return "\n".join(part for part in out if part != "")


def _is_record_value(value: Any) -> bool:
    """A dict reached as a *value* (record) vs. a nesting table. The
    model addresses keys by dotted path, so any dict appearing as a
    leaf — produced by the writer only when a value itself is a table —
    is serialised inline. Heuristic: an empty dict, or one written as a
    stored value, has no further dotted addressing under it. Here all
    dicts coming from :func:`_split_nested` are nesting tables, so this
    stays ``False``; it exists as the explicit seam for inline records."""

    return False


def _toml_value(value: Any) -> str:
    """Serialise a scalar/array/inline-table value."""

    if isinstance(value, dict):
        inner = ", ".join(
            f"{k} = {_toml_value(v)}" for k, v in sorted(value.items())
        )
        return "{" + inner + "}"
    return _toml_scalar(value)


class ProductConfig:
    """Python projection of the product runtime config entry.

    Wraps a language-agnostic :class:`ConfigStore`; this class only adds
    the Python ecosystem's file I/O (``tomllib`` read + the minimal
    deterministic writer above). ``set`` updates the external layer via
    the model and re-serialises the user-managed file so a write is
    immediately reflected on disk and in the next ``get``.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        inlined: Optional[Dict[str, Any]] = None,
        injected: Optional[Dict[str, Any]] = None,
        policies: Optional[Dict[str, ItemPolicy]] = None,
    ):
        self._path = path
        # The pure-internal (d) catalogue is created lazily and kept on
        # the side: it is *not* wired into the ConfigStore below, so the
        # runtime read/merge path provably cannot reach a d item. A
        # production projection could skip building it entirely.
        self._dev_internal: Optional[DevInternalRegistry] = None
        external: Dict[str, Any] = {}
        if path is not None:
            try:
                with open(path, "rb") as fh:
                    external = _flatten_nested(tomllib.load(fh))
            except FileNotFoundError:
                external = {}
        layered = LayeredConfig(
            inlined=dict(inlined or {}),
            external=external,
            injected=dict(injected or {}),
        )
        self._store = ConfigStore(layered, policies=policies)

    @property
    def store(self) -> ConfigStore:
        return self._store

    def declare(self, key: str, policy: ItemPolicy) -> None:
        self._store.declare(key, policy)

    # -- code-layer inline / hidden TOML (layer b) ----------------------
    #
    # An explicit code-level call (not a TOML directive, not automatic
    # build behaviour) that says "this config block ships *inside* the
    # artifact" so it no longer needs to sit as a scattered external
    # file. The model only ever sees decoded data; this bridge owns the
    # tomllib decode and the symmetric restore back to TOML text.

    def declare_inlined_toml(self, source: Any) -> None:
        """Embed a TOML config block as the inlined (b) default.

        ``source`` may be TOML *text* (``str``/``bytes``) — decoded here
        with the ecosystem ``tomllib`` parser — or an already-decoded
        mapping. After this call the product needs no external file for
        these defaults, yet every embedded item still enumerates through
        ``keys``/``query``/``query_resolved`` exactly like any ``b``
        value: embedding hides the *file*, never the *items*. ``a`` and
        ``c`` keep overriding ``b`` unchanged (no merge regression)."""

        if isinstance(source, (str, bytes)):
            text = source.decode() if isinstance(source, bytes) else source
            decoded = tomllib.loads(text)
        elif isinstance(source, dict):
            decoded = source
        else:
            raise TypeError(
                "declare_inlined_toml expects TOML text (str/bytes) or an "
                f"already-decoded mapping, got {type(source).__name__}"
            )
        self._store._cfg.install_inlined(_flatten_nested(decoded))

    def restore_inlined_toml(self) -> str:
        """Reconstruct the embedded (b) layer as equivalent TOML text.

        Embedding is not opacity: the inlined block is always
        recoverable to readable, hand-editable TOML. "Equivalent" means
        re-parsing the returned text yields the same decoded data the
        layer holds — guaranteed because it reuses the very same
        deterministic emitter the external file uses, over the same
        flat→nested transform, so encode∘decode is the identity for the
        scalar/array/table config vocabulary."""

        inlined = self._store._cfg.inlined
        nested = _split_nested(inlined)
        return _emit_toml(nested).strip() + ("\n" if nested else "")

    # -- pure-internal (d) declaration ----------------------------------
    #
    # d is only declarable in code and has *no* runtime config presence:
    # the call returns a plain value the caller binds as an ordinary
    # constant, and the dev metadata lands solely in a side registry the
    # store never consults. ``Layer.D`` stays out of RUNTIME_MERGE_ORDER,
    # so keys()/resolve_all()/query() cannot surface it by construction.

    @property
    def dev_internal(self) -> DevInternalRegistry:
        """The dev-phase-only catalogue of ``d`` declarations. Created
        on demand and kept off the runtime path; a runtime-only build
        may never touch it."""

        if self._dev_internal is None:
            self._dev_internal = DevInternalRegistry()
        return self._dev_internal

    def declare_internal(self, name: str, value: Any, tags=()) -> Any:
        """Declare a pure-internal datum and return the plain value.

        The return value is what the caller binds — byte-equivalent to a
        hand-written constant, carrying no config-system reference. Its
        only visibility is dev-phase tag/name lookup via
        :attr:`dev_internal`; it never enters the runtime store, so it
        is absent from ``keys``/``query``/``resolve``."""

        return self.dev_internal.declare(name, value, tags)

    def keys(self):
        return self._store.keys()

    def query(self, tags=None, credential_level: int = 0):
        """Tag- and read-tier-filtered key list. Pure passthrough to the
        language-agnostic store: the Python bridge adds no filtering
        logic of its own, it only exposes the model's one query API."""

        return self._store.query(tags, credential_level)

    def query_resolved(self, tags=None, credential_level: int = 0):
        return self._store.query_resolved(tags, credential_level)

    def max_read_level(self) -> int:
        return self._store.max_read_level()

    def read(self, key: str, credential_level: int = 0):
        return self._store.read(key, credential_level)

    # -- unified browse + dev-phase d listing ---------------------------
    #
    # Pure passthrough to the language-agnostic store: the row schema and
    # the tier routing live in the model so any host bridge browses
    # identically. ``dev_browse`` is a structurally separate listing of
    # the pure-internal (d) band sourced from the side registry — d is
    # not a runtime config item (it is promoted to a host constant), so
    # it never appears in ``browse`` at any level and is only ever
    # surfaced here, tag-searchable, for development.

    def browse(self, tags=None, credential_level: int = 0):
        """Self-describing rows for every runtime item within the
        caller's read tier. Takes a credential *level* only — no
        principal/identity — so the same level always yields the same
        browse. At :meth:`max_read_level` this is the complete runtime
        key set; d items are never included (they have no runtime
        presence). See :class:`BrowseEntry` for the per-row schema."""

        return self._store.browse(tags, credential_level)

    def dev_browse(self, tags=None):
        """The dev-phase-only catalogue of pure-internal (d) data.

        Explicitly *not* part of the runtime browse: d is promoted to a
        plain host constant and has zero runtime config footprint, so it
        is absent from :meth:`browse` at every level. This listing exists
        solely so a developer can discover a d datum by tag while
        building; a runtime-only build need never call it (and the
        registry it reads is off the runtime path entirely). When
        ``tags`` is given only d items whose tag set is a superset match
        (same freely-combinable tag-AND as the runtime query); otherwise
        every declared d item is listed. Returns a list of
        ``{"name", "value", "tags"}`` records, distinct in shape from a
        runtime :class:`BrowseEntry` so the two ranges never blur."""

        if self._dev_internal is None:
            # No d declared (or a runtime-only projection that never
            # built the registry) -> nothing to list, and crucially no
            # registry is created as a side effect of browsing.
            return []
        reg = self._dev_internal
        names = reg.search(tags) if tags else reg.names()
        out = []
        for name in names:
            item = reg.get(name)
            out.append(
                {
                    "name": item.name,
                    "value": item.value,
                    "tags": item.tags,
                }
            )
        return out

    def get(self, key: str, *args, **kwargs):
        return self._store.get(key, *args, **kwargs)

    def resolve(self, key: str):
        return self._store.resolve(key)

    def set(self, key: str, value: Any, credential_level: int = 0) -> None:
        """Validate + write through the model, then re-serialise the
        external layer to the user-managed file (when a path is set)."""

        self._store.set(key, value, credential_level=credential_level)
        if self._path is not None:
            self._write_external()

    def serialize_external(self) -> str:
        """The external (``a``) layer as deterministic TOML text — the
        exact bytes :meth:`set` writes to ``topo-app.toml``."""

        nested = _split_nested(self._store.pending_external())
        return _emit_toml(nested).strip() + ("\n" if nested else "")

    def _write_external(self) -> None:
        # Only reachable when a file-backed config is in use; a
        # pathless (in-memory) config never persists. Asserting the
        # invariant keeps the write honest rather than letting a
        # None path reach open() through some future caller.
        if self._path is None:
            raise RuntimeError(
                "cannot persist external layer: this config has no file path"
            )
        with open(self._path, "w", encoding="utf-8") as fh:
            fh.write(self.serialize_external())
