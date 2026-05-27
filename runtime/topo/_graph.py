"""In-memory logic graph: the single source of truth a topo-app program
builds by registration.

The graph is intentionally a plain data model with no behaviour beyond
structural equality. Emission, read-back and checking are separate
concerns that consume this model so the round-trip can be reasoned
about as data, not as side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# A field of a stdlib `record<...>` type: ordered name + topo type spelling.
RecordField = Tuple[str, "TypeRef"]


@dataclass(frozen=True)
class TypeRef:
    """A topo type as it will be spelled in `.topo`.

    `scalar` carries a stdlib scalar alias (``int`` / ``float`` / ``bool``
    / ``str``). `record` carries an ordered field list. Exactly one of the
    two is populated; `void` (no input / terminal) is represented by the
    absence of a TypeRef at the use site, never by a TypeRef instance.
    """

    scalar: Optional[str] = None
    record: Optional[Tuple[RecordField, ...]] = None

    def topo(self) -> str:
        if self.record is not None:
            inner = ", ".join(f"{n}: {t.topo()}" for n, t in self.record)
            return f"record<{inner}>"
        assert self.scalar is not None
        return self.scalar


@dataclass
class Handler:
    """A registered logic unit. ``In`` is None for a source handler."""

    name: str
    in_type: Optional[TypeRef]
    out_type: TypeRef

    def signature(self) -> str:
        # The single input parameter is conventionally named `in` to match
        # spec §7a's HandlerInput form; a source handler has no parameter.
        param = "" if self.in_type is None else f"{self.in_type.topo()} in"
        return f"handler {self.name}({param}) -> {self.out_type.topo()};"


@dataclass(frozen=True)
class Edge:
    """A pipeline edge inside a flow. `target` is None for a terminal
    edge (``source -> void;``)."""

    source: str
    target: Optional[str]

    @property
    def is_terminal(self) -> bool:
        return self.target is None


@dataclass
class Flow:
    name: str
    edges: List[Edge] = field(default_factory=list)


@dataclass
class Graph:
    """The whole program: namespace, handlers, one flow.

    A single namespace + single flow keeps the proof of concept minimal
    while still exercising every committed-to mapping rule.
    """

    namespace: str
    handlers: List[Handler] = field(default_factory=list)
    flow: Optional[Flow] = None

    def handler(self, name: str) -> Optional[Handler]:
        for h in self.handlers:
            if h.name == name:
                return h
        return None

    # --- Semantic equality (the round-trip's headline acceptance) -------

    def semantic_key(self):
        """A canonical, order-insensitive description of the graph's
        meaning. Two graphs are semantically equivalent iff their keys are
        equal. Handler order and edge order do not change meaning (the
        stage topology is derived from the edge set), so both are sorted.
        """

        handlers = sorted(
            (
                h.name,
                None if h.in_type is None else h.in_type.topo(),
                h.out_type.topo(),
            )
            for h in self.handlers
        )
        flow_name = self.flow.name if self.flow else None
        edges = (
            sorted((e.source, e.target) for e in self.flow.edges)
            if self.flow
            else []
        )
        return (self.namespace, flow_name, tuple(handlers), tuple(edges))

    def equivalent_to(self, other: "Graph") -> bool:
        return self.semantic_key() == other.semantic_key()
