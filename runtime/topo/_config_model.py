"""Language-agnostic core of the product runtime configuration: the
layered value model, its merge precedence, and per-value provenance.

This is the *substance layer* — it owns semantics, not wiring. It deliberately has
no TOML parser, no file I/O and no Python-specific behaviour. Each
host-language bridge is responsible for decoding its ecosystem's TOML
into the plain dict/scalar values this model consumes, and for
projecting the merged result into idiomatic host accessors. The model
itself would read identically if reimplemented in another host runtime.

Why the product config is a *separate file from* the build-time
``Topo.toml``
-----------------------------------------------------------------------
``Topo.toml`` configures the *toolchain build* (host language, sources,
optimisation feature-modes, check policy — owned by topo-build). This
model configures the *built product's* runtime/logic behaviour. They
live at different lifecycle layers and answer different questions
("how is it compiled" vs. "how does the running product behave"), so
they are kept as two files with no shared or overlapping sections. The
fixed name for the product runtime config in this proof of concept is
``topo-app.toml``. A build-toolchain key has exactly one home —
``Topo.toml`` — and putting it into the product config is a category
error the validation hook rejects with a message pointing the user back
to ``Topo.toml`` (instead of silently accepting a key nothing reads).

The three runtime layers and their precedence
---------------------------------------------
Three layers carry a configuration value at runtime, ordered from least
to most explicit:

* ``b`` — inlined / hidden TOML embedded in the artifact via an explicit
  code-layer declaration. Acts as the built-in default.
* ``a`` — the external ``topo-app.toml`` file the user manages. Overrides
  the inlined default.
* ``c`` — a value injected directly in code through the topo interface.
  The most explicit, overrides everything.

Frozen merge precedence (user-confirmed):

    inlined default (b)  ◁  external file (a)  ◁  in-code injection (c)

"more explicit wins": ``c`` overrides ``a`` overrides ``b``, per key.

A fourth band ``d`` (pure-internal) exists in the model's vocabulary but
is intentionally *absent from this runtime merge*: ``d`` is not "a
configuration value that happens to be hidden" — it is promoted to a
plain host variable/constant by the toolchain and has zero
configuration-system footprint at runtime. There is nothing to merge
because at runtime it is no longer a config value at all. Resolving an
effective value is therefore an a/b/c-only operation by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum, IntEnum
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Fixed product runtime config filename for this proof of concept. Kept
# here (not in a bridge) so every host agrees on the boundary name.
PRODUCT_CONFIG_FILENAME = "topo-app.toml"

# The build toolchain owns Topo.toml; these are its section names. A key
# whose first dotted segment is one of these belongs to the build config,
# never to the product runtime config. Naming them here keeps the
# non-overlap boundary a single explicit list rather than scattered
# string checks.
BUILD_TOOLCHAIN_SECTIONS = frozenset(
    {
        "topo",
        "build",
        "builder",
        "parallel",
        "adaptive",
        "optimize",
        "observability",
        "lifetime",
        "loop_parallel",
        "types",
        "completeness",
        "check",
        "test",
    }
)


class Layer(Enum):
    """Which runtime layer a value originates from.

    The enum *values* encode the merge precedence (higher wins) so the
    merge never hard-codes an ordering separate from the layer identity.
    ``D`` is listed for vocabulary completeness but is never produced by
    the runtime merge (see module docstring).
    """

    B = 1  # inlined / hidden TOML default embedded in the artifact
    A = 2  # external topo-app.toml the user manages
    C = 3  # in-code explicit injection through the topo interface
    D = 0  # pure-internal; promoted to code, never merged at runtime


# Layers that participate in the runtime merge, least to most explicit.
RUNTIME_MERGE_ORDER: Tuple[Layer, ...] = (Layer.B, Layer.A, Layer.C)


class BuildConfigKeyError(ValueError):
    """Raised when a key that belongs to the build toolchain is offered
    to the product runtime config. The message names ``Topo.toml`` so
    the user is told exactly where the key actually belongs."""


def _root_section(key: str) -> str:
    """First dotted segment of a config key (``a.b.c`` -> ``a``)."""

    return key.split(".", 1)[0]


def reject_if_build_config_key(key: str) -> None:
    """Boundary guard: refuse a key that belongs in ``Topo.toml``.

    The product runtime config and the build-time ``Topo.toml`` share no
    sections by design; accepting a build key here would create a second,
    silently-ignored home for it. Rejecting loudly — and naming the file
    the key actually belongs to — keeps the boundary honest.
    """

    section = _root_section(key)
    if section in BUILD_TOOLCHAIN_SECTIONS:
        raise BuildConfigKeyError(
            f"'{key}' configures the build toolchain (section "
            f"'[{section}]') and belongs in Topo.toml, not the product "
            f"runtime config ({PRODUCT_CONFIG_FILENAME}). The two files "
            f"share no sections; set this in Topo.toml instead."
        )


@dataclass(frozen=True)
class ResolvedValue:
    """An effective value plus the layer it came from.

    Provenance travels with every value so any consumer (a human, an
    agent, a later read/write slice) can answer "which layer set this?"
    without re-running the merge.
    """

    value: Any
    layer: Layer


class _NoDefault:
    """Sentinel marking "this item has no built-in (inlined ``b``)
    default". Distinct from a stored ``None`` so a browse consumer can
    tell "no default exists" from "default is a null-like value"."""

    def __repr__(self) -> str:  # pragma: no cover - display aid only
        return "<no default>"

    def __bool__(self) -> bool:
        return False


# A single shared instance so equality/identity checks are stable for
# consumers ("is this row's default the no-default marker?").
_NO_DEFAULT = _NoDefault()


@dataclass(frozen=True)
class BrowseEntry:
    """One self-describing row of the unified browse.

    Carries everything a human or an agent needs to judge a config item
    without a second query: its identity and contract type, the built-in
    default and the current effective value with the layer that produced
    it, the write blast-radius (``impact``) and *both* permission
    thresholds — ``required_write_level`` (the mis-operation gate) and
    ``required_read_level`` (the read-visibility tier) — kept as separate
    fields because the two roles are orthogonal, plus the
    freely-combinable retrieval ``tags``. ``default`` is the
    :data:`_NO_DEFAULT` sentinel when the item has no inlined default.
    Frozen so a browse row cannot be mutated by a consumer.
    """

    key: str
    type: str
    default: Any
    effective: Any
    layer: Layer
    impact: "ImpactLevel"
    required_write_level: int
    required_read_level: int
    tags: frozenset


@dataclass
class LayeredConfig:
    """The a/b/c layers as plain decoded data + the merge over them.

    Each layer is a flat mapping of dotted-key -> already-decoded plain
    value (scalar / list / dict). TOML parsing is a separate concern: a
    bridge fills these maps; this model only merges and attributes them.
    """

    inlined: Dict[str, Any] = field(default_factory=dict)  # layer b
    external: Dict[str, Any] = field(default_factory=dict)  # layer a
    injected: Dict[str, Any] = field(default_factory=dict)  # layer c

    def install_inlined(self, data: Dict[str, Any]) -> None:
        """Register a block of already-decoded data as the inlined (b)
        layer — the artifact-embedded default.

        This is the model side of an explicit code-layer declaration that
        a config block travels *inside the artifact* instead of as a
        scattered external file. It is deliberately decode-only: the
        caller (a host bridge) is responsible for turning TOML text into
        this plain map and, symmetrically, for restoring the map back to
        equivalent TOML. Embedding changes where the *file* lives, never
        whether the *items* are browsable: the installed keys merge as
        the ordinary ``b`` default, so every later enumeration still
        lists them subject only to the normal tier/tag rules.

        Build-toolchain keys are rejected here too, so a misplaced key
        cannot sneak in through the embedded layer any more than through
        the external file.
        """

        for key in data:
            reject_if_build_config_key(key)
        self.inlined = dict(data)

    def _layer_map(self, layer: Layer) -> Dict[str, Any]:
        if layer is Layer.B:
            return self.inlined
        if layer is Layer.A:
            return self.external
        if layer is Layer.C:
            return self.injected
        # Layer.D never participates in the runtime merge by construction.
        raise AssertionError(f"{layer} is not a runtime merge layer")

    def _validate_keys(self) -> None:
        for layer in RUNTIME_MERGE_ORDER:
            for key in self._layer_map(layer):
                reject_if_build_config_key(key)

    def keys(self) -> List[str]:
        """Every key contributed by any runtime layer, sorted for a
        stable, hand-checkable enumeration."""

        seen = set()
        for layer in RUNTIME_MERGE_ORDER:
            seen.update(self._layer_map(layer))
        return sorted(seen)

    def resolve(self, key: str) -> ResolvedValue:
        """Effective value + provenance for one key.

        Walks the layers least-to-most explicit; the last layer that
        carries the key wins, and that layer is the recorded provenance.
        """

        reject_if_build_config_key(key)
        winner: Optional[ResolvedValue] = None
        for layer in RUNTIME_MERGE_ORDER:
            layer_map = self._layer_map(layer)
            if key in layer_map:
                winner = ResolvedValue(value=layer_map[key], layer=layer)
        if winner is None:
            raise KeyError(key)
        return winner

    def resolve_all(self) -> Dict[str, ResolvedValue]:
        """The unified result: every key -> (effective value, provenance
        layer). Build-toolchain keys are rejected up front so a misplaced
        key fails loudly rather than appearing as a phantom entry."""

        self._validate_keys()
        return {key: self.resolve(key) for key in self.keys()}


def merge_layers(
    inlined: Optional[Dict[str, Any]] = None,
    external: Optional[Dict[str, Any]] = None,
    injected: Optional[Dict[str, Any]] = None,
) -> Dict[str, ResolvedValue]:
    """Convenience: build a :class:`LayeredConfig` from the three layer
    maps and return the resolved key -> value+provenance mapping."""

    cfg = LayeredConfig(
        inlined=dict(inlined or {}),
        external=dict(external or {}),
        injected=dict(injected or {}),
    )
    return cfg.resolve_all()


def iter_provenance(
    resolved: Dict[str, ResolvedValue],
) -> Iterable[Tuple[str, Any, Layer]]:
    """Flatten a resolved mapping to ``(key, value, layer)`` triples in
    stable key order — the shape later browse/introspection slices read.
    """

    for key in sorted(resolved):
        rv = resolved[key]
        yield key, rv.value, rv.layer


# --- Value-type contract ------------------------------------------------
#
# A config value only enters the model if it has a stdlib bridge type, so
# every value the running product reads has a known contract — the same
# schema vocabulary the handler In/Out boundary uses. The mapping is
# expressed in terms of decoded plain data (the shape every host bridge
# normalises its TOML into), so the rule reads identically in any host.

# TOML scalar/aggregate -> stdlib bridge spelling. Stated as a single
# explicit table so the contract is one list, not scattered isinstance
# chains. ``bool`` is checked before ``int`` because Python's ``bool`` is
# an ``int`` subclass and the two carry different stdlib contracts.
STDLIB_TYPE_MAP: Tuple[Tuple[type, str], ...] = (
    (bool, "bool"),
    (int, "int"),      # TOML integer -> i64
    (float, "float"),  # TOML float   -> f64
    (str, "str"),
    (list, "slice"),
    (dict, "record"),
)


class UnbridgedValueError(TypeError):
    """A config value whose type has no stdlib bridge was offered.

    The message names the offending key and points at the
    stdlib-bridging-types gap so the rejection is actionable (e.g. a
    TOML datetime: ``time_*`` is not yet a stdlib type, so accepting it
    would mean a value with no contract). Silently keeping such a value
    would leave the product reading something nothing in the schema
    describes — louder is safer than a phantom contract.
    """


def stdlib_type_of(value: Any) -> str:
    """The stdlib bridge spelling for a decoded value, or raise.

    Aggregates (list/dict) are validated element-wise so a datetime
    smuggled inside an array or table is caught, not just a top-level
    one. The raised :class:`UnbridgedValueError` carries no key on its
    own; callers that know the key re-raise with it attached.
    """

    # date/time/datetime have no stdlib correspondence — the stdlib
    # ``time_*`` family is not yet bridged. Reject rather than invent an
    # ad-hoc contract.
    if isinstance(value, (datetime, date, time)):
        raise UnbridgedValueError(
            "value of type "
            f"'{type(value).__name__}' has no stdlib bridge type — TOML "
            "date/time maps to the not-yet-implemented time_* family "
            "(stdlib-bridging-types gap: time_* / uuid / decimal128 are "
            "not yet bridged). Accepting it would store a value with no "
            "schema contract; use a bridged scalar instead."
        )
    for py_type, spelling in STDLIB_TYPE_MAP:
        if isinstance(value, py_type):
            if py_type is list:
                for element in value:
                    stdlib_type_of(element)
            elif py_type is dict:
                for element in value.values():
                    stdlib_type_of(element)
            return spelling
    raise UnbridgedValueError(
        f"value of type '{type(value).__name__}' has no stdlib bridge "
        "type (stdlib-bridging-types gap). Only string / integer / "
        "float / bool / array / table values have a schema contract; "
        "refusing to store an uncontracted value."
    )


def validate_value(key: str, value: Any) -> None:
    """Type-gate a value about to be written under ``key``.

    Re-raises the underlying :class:`UnbridgedValueError` with the
    offending key prepended so a rejection always locates the problem.
    """

    try:
        stdlib_type_of(value)
    except UnbridgedValueError as exc:
        raise UnbridgedValueError(f"config key '{key}': {exc}") from None


# --- Write protection: impact level + credential gate -------------------
#
# This gate exists to stop *mistaken* writes to items where a wrong value
# has outsized blast radius — it is a guard rail, not a secrecy boundary.
# It is identity-independent by construction: the check takes a credential
# *level*, never a principal. A human and an agent presenting the same
# level are treated identically; there is no "who" argument anywhere.


class ImpactLevel(IntEnum):
    """How disruptive a wrong write to a config item is.

    Modelled as an *ordered* scale (not a bool) from the start so a later
    multi-tier permission slice can introduce intermediate levels and a
    per-item required-credential-level without reshaping callers: today
    only the LOW/HIGH endpoints are used and the gate compares the
    presented credential level against the item's required level.
    """

    LOW = 0   # routine; a wrong value is easily noticed and reverted
    HIGH = 1  # outsized blast radius; a careless write must be deliberate


# Credential level a writer must present to pass the gate for an item of
# a given impact. Kept as an explicit ordered map (not ``impact == HIGH``)
# so inserting a mid level later is a table edit, not a logic rewrite.
_REQUIRED_CREDENTIAL_LEVEL: Dict[ImpactLevel, int] = {
    ImpactLevel.LOW: 0,
    ImpactLevel.HIGH: 1,
}

# A writer with no credential is level 0 — enough for LOW items, short of
# anything that requires deliberate intent.
NO_CREDENTIAL_LEVEL = 0


class WriteProtectionError(PermissionError):
    """A write to an item was refused because the presented credential
    level is below what the item's impact level requires. The message is
    about the credential gap only — never about identity."""


@dataclass(frozen=True)
class ItemPolicy:
    """Per-item declaration carrying two *orthogonal* dimensions.

    * ``tags`` — a freely-combinable set of strings scoping *retrieval*.
      It is a pure label set: tags never affect read or write
      permission, only which filter a query matches. Stored as a
      ``frozenset`` so the policy stays hashable/frozen and tag identity
      is order-independent.
    * ``read_level`` — the minimum permission level a caller must present
      to have this item *enumerated or read*. Default ``0`` means the
      item is visible to everyone (an ordinary, non-permission item).
      A value above ``0`` makes the item permission-gated: hidden from
      any query below that level, listed only at/above it.
    * ``impact`` — independent of the two above: it drives the *write*
      mis-operation gate (a wrong write's blast radius), not visibility.

    The two permission roles (read-visibility tiering via ``read_level``
    and the write gate via ``impact``) ride the same integer scale but
    are deliberately separate fields: an item can be freely readable yet
    write-guarded, or read-gated yet low-impact to write. Tags are a
    third, permission-independent axis. Keeping ``ItemPolicy`` a frozen
    dataclass means these extra fields attach to the same declaration
    object without reshaping any call site.
    """

    impact: ImpactLevel = ImpactLevel.LOW
    tags: frozenset = frozenset()
    read_level: int = 0

    def __post_init__(self):
        # Accept any iterable of tag strings at the call site but always
        # store a frozenset, so tag identity is order-independent and the
        # dataclass stays hashable. object.__setattr__ because frozen.
        if not isinstance(self.tags, frozenset):
            object.__setattr__(self, "tags", frozenset(self.tags))


def required_credential_level(policy: ItemPolicy) -> int:
    """The minimum credential level a writer must present for this item."""

    return _REQUIRED_CREDENTIAL_LEVEL[policy.impact]


def required_read_level(policy: ItemPolicy) -> int:
    """The minimum permission level a caller must present to have this
    item enumerated/read. ``0`` means unrestricted (a non-permission
    item that any caller, credentialled or not, can see).

    This is the *read-visibility tiering* role — the orthogonal twin of
    :func:`required_credential_level` (the write gate). Both consult the
    same integer scale; they answer different questions (may I *see* it
    vs. may I *change* it) and never collapse into one another."""

    return policy.read_level


def authorize_write(
    key: str,
    policy: ItemPolicy,
    credential_level: int = NO_CREDENTIAL_LEVEL,
) -> None:
    """Pass iff ``credential_level`` meets the item's required level.

    Note the signature: there is no principal/identity parameter. The
    gate cannot and does not distinguish a human from an agent — it only
    compares levels, which is exactly the "mis-operation guard, not secrecy" intent.
    """

    needed = required_credential_level(policy)
    if credential_level < needed:
        raise WriteProtectionError(
            f"config key '{key}' is impact={policy.impact.name}; writing "
            f"it requires credential level >= {needed}, but the write "
            f"presented level {credential_level}. This guard prevents "
            "accidental high-impact changes; re-issue the write with a "
            "sufficient credential level if the change is intended."
        )


# --- Read/write API over the layered model ------------------------------


class _Missing:
    """Sentinel so ``get(key)`` can distinguish 'no default given' from a
    legitimately stored ``None``-like value."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "<no default>"


_MISSING = _Missing()


class ConfigStore:
    """Read/write façade over :class:`LayeredConfig`.

    Reads honour the frozen ``b ◁ a ◁ c`` precedence. Writes land in the
    *external* layer (``a``) — the user-managed file's in-memory image —
    because that is the layer a user/agent is allowed to author; the
    inlined default (``b``) and in-code injection (``c``) are owned by
    other mechanisms. This class stays language-agnostic: it mutates the
    decoded ``external`` map and reports the new value; turning that map
    into ``topo-app.toml`` bytes is a host-bridge concern, not the
    model's. A host bridge calls :meth:`pending_external` to obtain the
    map to serialise after a write.
    """

    def __init__(
        self,
        layered: Optional[LayeredConfig] = None,
        policies: Optional[Dict[str, ItemPolicy]] = None,
    ):
        self._cfg = layered if layered is not None else LayeredConfig()
        # Unlisted items default to LOW impact: writes are unguarded
        # unless an item is explicitly declared high-impact.
        self._policies: Dict[str, ItemPolicy] = dict(policies or {})

    # -- declaration -----------------------------------------------------

    def declare(self, key: str, policy: ItemPolicy) -> None:
        """Attach a write-protection policy to ``key``."""

        reject_if_build_config_key(key)
        self._policies[key] = policy

    def policy_of(self, key: str) -> ItemPolicy:
        """The item's declared policy, or the LOW-impact default."""

        return self._policies.get(key, ItemPolicy())

    # -- tag + read-visibility query ------------------------------------
    #
    # One query API, two orthogonal filter dimensions, *zero* ambient
    # state. It takes the filter (tags, level) as arguments and reads no
    # identity — so the same method called from two sites with different
    # arguments yields different visibility purely from what each site
    # passes in. There is intentionally no principal/user/agent argument:
    # the scale is consulted by level only.

    def max_read_level(self) -> int:
        """The highest read-level any runtime item requires.

        A caller presenting this level (or above) can enumerate *every*
        runtime item — there is no level at which some runtime fragment
        stays invisible. This is what makes the tiered-transparency
        invariant checkable: the top of the scale always sees the whole
        runtime range. ``0`` when nothing is permission-gated."""

        levels = [
            self.policy_of(key).read_level for key in self._cfg.keys()
        ]
        return max(levels) if levels else 0

    def _visible(self, key: str, credential_level: int) -> bool:
        return credential_level >= self.policy_of(key).read_level

    def query(
        self,
        tags: Optional[Iterable[str]] = None,
        credential_level: int = NO_CREDENTIAL_LEVEL,
    ) -> List[str]:
        """Keys matching a tag filter *and* within the caller's read tier.

        Filtering composes two independent axes:

        * ``tags`` — when ``None`` (or empty) every item matches the tag
          axis; otherwise an item matches only if its tag set is a
          *superset* of the requested set (tag AND, freely combinable).
          Tags never grant or deny permission; they only scope range.
        * ``credential_level`` — an item is listed only when this level
          meets the item's ``read_level``. With no credential, every
          permission-gated item (``read_level > 0``) is hidden — the
          "no tag = all, except permission-gated items default hidden"
          rule. At :meth:`max_read_level` the read axis admits all keys,
          so the result is the complete runtime range (tag filter aside).

        Returns sorted keys for a stable, hand-checkable enumeration."""

        wanted = frozenset(tags) if tags else frozenset()
        out = []
        for key in self._cfg.keys():
            if not self._visible(key, credential_level):
                continue
            if wanted and not wanted.issubset(self.policy_of(key).tags):
                continue
            out.append(key)
        return sorted(out)

    def query_resolved(
        self,
        tags: Optional[Iterable[str]] = None,
        credential_level: int = NO_CREDENTIAL_LEVEL,
    ) -> Dict[str, ResolvedValue]:
        """:meth:`query` but returning effective value + provenance for
        each matched key — the read counterpart of the filter, so a
        caller within its tier gets values too, not just names."""

        return {
            key: self._cfg.resolve(key)
            for key in self.query(tags, credential_level)
        }

    # -- read ------------------------------------------------------------

    def keys(self) -> List[str]:
        """Every key any runtime layer contributes, sorted."""

        return self._cfg.keys()

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Effective value honouring ``b ◁ a ◁ c``.

        Returns ``default`` if given and the key is set by no layer;
        otherwise a missing key raises ``KeyError`` (no silent ``None``).
        """

        try:
            return self._cfg.resolve(key).value
        except KeyError:
            if default is _MISSING:
                raise
            return default

    def resolve(self, key: str) -> ResolvedValue:
        """Effective value + which layer it came from."""

        return self._cfg.resolve(key)

    def resolve_all(self) -> Dict[str, ResolvedValue]:
        """Every key -> (effective value, provenance layer)."""

        return self._cfg.resolve_all()

    def read(
        self,
        key: str,
        credential_level: int = NO_CREDENTIAL_LEVEL,
    ) -> Any:
        """Read honouring the read-visibility tier.

        Below the item's ``read_level`` the item is treated as not
        listable, so a read is refused the same way enumeration hides it
        — a permission-gated item must not be reachable by value either.
        :meth:`get` stays the raw, tier-blind accessor lower layers and
        the existing write path rely on; ``read`` is the tier-aware door.
        """

        if not self._visible(key, credential_level):
            needed = self.policy_of(key).read_level
            raise WriteProtectionError(
                f"config key '{key}' requires read level >= {needed} to "
                f"be listed or read; the request presented level "
                f"{credential_level}. Permission-gated items are hidden "
                "below their tier; re-issue with a sufficient level."
            )
        return self._cfg.resolve(key).value

    # -- write -----------------------------------------------------------

    def set(
        self,
        key: str,
        value: Any,
        credential_level: int = NO_CREDENTIAL_LEVEL,
    ) -> None:
        """Write ``value`` for ``key`` into the external layer (``a``).

        Order of checks: a build-toolchain key is a category error
        (rejected first); then the value must have a stdlib contract;
        then the write-protection gate. Only after all three pass is the
        external map mutated, so a rejected write never leaves a partial
        state.
        """

        reject_if_build_config_key(key)
        validate_value(key, value)
        authorize_write(key, self.policy_of(key), credential_level)
        self._cfg.external[key] = value

    # -- unified browse + introspection ---------------------------------
    #
    # A single call that yields, *within the caller's read tier*, a
    # self-describing row per config item — enough for a human or an
    # agent to judge "what does changing this affect / is it high-impact
    # / what level do I need to see and to write it" without a second
    # round trip. It is built strictly on the tier-aware door
    # (``query_resolved`` -> ``query`` -> ``policy_of``); it never calls
    # the tier-blind ``resolve_all``/``resolve``/``get``, so a
    # permission-gated item cannot leak into a lower-level caller's view.
    # The row set is derived live from the model on every call, so a key
    # declared after construction appears with no list to maintain.

    def browse(
        self,
        tags: Optional[Iterable[str]] = None,
        credential_level: int = NO_CREDENTIAL_LEVEL,
    ) -> List["BrowseEntry"]:
        """Self-describing rows for every item in the caller's read tier.

        Routes through :meth:`query_resolved` (the tier-aware door), so
        the result is exactly this level's complete range: at
        :meth:`max_read_level` every runtime item is present (the
        tiered-transparency invariant — no level-invisible fragment);
        below an item's ``read_level`` that item is wholly absent, value
        included. The signature takes a credential *level* only — there
        is no principal/identity argument, so the same level yields the
        same browse regardless of any caller notion. ``d`` is not a
        runtime item and never appears here; it has its own dev-phase
        listing on the registry.

        Each row carries: ``key``; the stdlib-contract ``type`` derived
        from the effective (else default) value; the ``default`` (the
        inlined ``b`` value when present, else a sentinel); the
        ``effective`` value; its provenance ``layer`` (a/b/c); the
        ``impact`` level; the ``required_write_level`` (the write gate)
        and ``required_read_level`` (the read tier) so both permission
        roles are explicit; and the freely-combinable ``tags``.
        """

        resolved = self.query_resolved(tags, credential_level)
        rows: List[BrowseEntry] = []
        for key in sorted(resolved):
            rv = resolved[key]
            policy = self.policy_of(key)
            # Default = the inlined (b) built-in when the key has one;
            # absent otherwise. Read from the b layer map directly (not
            # via the tier-blind resolve), so this stays a pure lookup
            # that cannot widen visibility.
            has_default = key in self._cfg.inlined
            default_value: Any = (
                self._cfg.inlined[key] if has_default else _NO_DEFAULT
            )
            # Type from the contract that already governs every stored
            # value; prefer the effective value, fall back to the
            # default so a row still types when both exist.
            type_source = (
                rv.value if rv.value is not None else default_value
            )
            try:
                value_type = stdlib_type_of(type_source)
            except UnbridgedValueError:
                value_type = stdlib_type_of(rv.value)
            rows.append(
                BrowseEntry(
                    key=key,
                    type=value_type,
                    default=default_value,
                    effective=rv.value,
                    layer=rv.layer,
                    impact=policy.impact,
                    required_write_level=required_credential_level(policy),
                    required_read_level=required_read_level(policy),
                    tags=policy.tags,
                )
            )
        return rows

    # -- bridge hook -----------------------------------------------------

    def pending_external(self) -> Dict[str, Any]:
        """The external-layer map a host bridge serialises to the
        user-managed config file after a write. Returned by reference so
        the bridge sees the post-write image; the model never touches
        files itself."""

        return self._cfg.external


# --- Pure-internal (d) band: dev-phase registry, no runtime presence ----
#
# ``d`` is the innermost band. Unlike a/b/c it is *not* a runtime config
# value: after toolchain processing it is promoted to a plain host
# variable/constant with zero configuration-system footprint, which is
# why ``Layer.D`` is excluded from RUNTIME_MERGE_ORDER and the runtime
# merge above never sees it. Its tags exist for one purpose only — being
# discoverable *while developing* — so it gets its own registry that is
# structurally disjoint from ConfigStore/LayeredConfig. Nothing on the
# runtime read/merge path holds a reference to this type or its
# instances; a runtime build can drop this registry entirely without
# changing any resolved value.


@dataclass(frozen=True)
class DevInternalItem:
    """A pure-internal datum as seen *only during development*.

    Carries the declared name, its constant value, and dev-phase
    retrieval tags. It has no ``read_level``/``impact``: those gate
    runtime visibility and write blast-radius, and ``d`` has neither a
    runtime presence nor a runtime write path. This object lives solely
    in :class:`DevInternalRegistry`; the runtime config store has no
    field that can hold it.
    """

    name: str
    value: Any
    tags: frozenset = frozenset()

    def __post_init__(self):
        if not isinstance(self.tags, frozenset):
            object.__setattr__(self, "tags", frozenset(self.tags))


class DevInternalRegistry:
    """A development-phase-only catalogue of ``d`` declarations.

    This is the *only* place a ``d`` item is visible, and it is
    deliberately a free-standing object the runtime config path never
    consults: ConfigStore does not hold one, LayeredConfig does not
    reference one, and ``resolve``/``query``/``keys`` cannot reach it.
    Its sole job is to let a developer find a pure-internal datum by
    name or tag while building. The honest projection in a dynamic host:
    declaring a ``d`` returns a plain value the caller binds as an
    ordinary constant, and the dev metadata is recorded here and nowhere
    the runtime looks. A production build may simply never construct
    this registry — the promoted constants stand on their own.
    """

    def __init__(self) -> None:
        self._items: Dict[str, DevInternalItem] = {}

    def declare(
        self,
        name: str,
        value: Any,
        tags: Iterable[str] = (),
    ) -> Any:
        """Record a pure-internal datum for dev-phase discovery and
        return the plain value to be bound as a host constant.

        The value still must satisfy the stdlib contract (same schema
        vocabulary as everything else), but it is *not* stored as a
        config item anywhere: the return value is what the caller binds,
        byte-equivalent to a hand-written constant. The build-toolchain
        boundary guard applies to the name as well, so ``d`` cannot be
        used to smuggle a build key either.
        """

        reject_if_build_config_key(name)
        validate_value(name, value)
        self._items[name] = DevInternalItem(
            name=name, value=value, tags=frozenset(tags)
        )
        return value

    def names(self) -> List[str]:
        """Every declared ``d`` name, sorted — dev-phase enumeration."""

        return sorted(self._items)

    def get(self, name: str) -> DevInternalItem:
        """The dev-phase record for ``name`` (raises if undeclared)."""

        return self._items[name]

    def search(self, tags: Iterable[str]) -> List[str]:
        """``d`` names whose tag set is a superset of ``tags`` (tag AND,
        same freely-combinable semantics as the runtime tag query) —
        this is the *only* retrieval ``d``'s tags ever serve."""

        wanted = frozenset(tags)
        return sorted(
            name
            for name, item in self._items.items()
            if wanted.issubset(item.tags)
        )
