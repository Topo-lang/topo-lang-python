"""topo-app Python surface: idiomatic decorators, not a macro DSL.

The shared topo-app philosophy (Functor model + the topo-app principles)
is fixed in the toolchain; each topo-lang projects it onto its own
idioms. The Python projection is the decorator + plain registration
call — registering a handler is ordinary Python, In/Out are read from
annotations, and a flow is declared by listing the chain. No new
syntax, no metaclass magic.

A handler stays a normal callable after registration, so it remains
independently invocable and unit-testable with zero framework bootstrap
— a free consequence of the Functor model, not extra design.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from ._graph import Edge, Flow, Graph, Handler
from ._reflect import reflect_signature


class App:
    """A topo-app program: the in-memory logic graph plus the callables.

    One App owns one namespace and (for this proof of concept) one
    flow — enough to exercise every mapping rule without productionizing.
    """

    def __init__(self, namespace: str):
        self._graph = Graph(namespace=namespace)
        self._fns: dict[str, Callable] = {}

    # --- registration ---------------------------------------------------

    def handler(self, fn: Optional[Callable] = None):
        """Register a logic unit. Usable bare (``@app.handler``) since the
        signature carries everything; In/Out are reflected, never
        re-declared (no declaration is written twice — once in code and
        once by hand)."""

        def register(f: Callable) -> Callable:
            in_type, out_type = reflect_signature(f)
            self._graph.handlers.append(
                Handler(name=f.__name__, in_type=in_type, out_type=out_type)
            )
            self._fns[f.__name__] = f
            return f  # unchanged: still a plain, independently callable fn

        return register if fn is None else register(fn)

    def flow(self, name: str, *stages) -> None:
        """Declare a linear logic chain: ``flow("p", a, b, c)`` becomes
        edges a->b->c->void. ``parallel(...)`` members fan in/out from the
        same neighbours (same-source / same-sink == same-stage parallel
        candidates, per the topo-app mapping table)."""

        edges: List[Edge] = []
        stages = list(stages)

        def names(stage) -> List[str]:
            if isinstance(stage, _Parallel):
                return [m.__name__ for m in stage.members]
            return [stage.__name__]

        for i in range(len(stages) - 1):
            for src in names(stages[i]):
                for tgt in names(stages[i + 1]):
                    edges.append(Edge(src, tgt))
        for src in names(stages[-1]):
            edges.append(Edge(src, None))  # terminal -> void

        self._graph.flow = Flow(name=name, edges=edges)

    # --- introspection / round-trip ------------------------------------

    @property
    def graph(self) -> Graph:
        return self._graph

    def callable_for(self, name: str) -> Optional[Callable]:
        return self._fns.get(name)


class _Parallel:
    def __init__(self, members):
        self.members = members


def parallel(*members) -> _Parallel:
    """Independent units on the same input == same-stage parallel
    candidates (per the topo-app same-stage parallelism rule). Purity of
    these is enforced by core PurityCheck after emission, not
    self-asserted."""

    return _Parallel(members)
