"""``topo.Record`` — an ergonomic spelling for a stdlib ``record<...>``.

The spec maps a Python host ``record<...>`` to an order-preserving
``tuple[...]``. Writing ``tuple[Annotated[int, "id"], Annotated[float,
"amount"]]`` by hand is correct but noisy, so ``Record`` builds exactly
that annotation from ``(name, type)`` pairs while keeping field order
explicit and stable.

    Record[("id", int), ("amount", float)]
    # == tuple[Annotated[int, "id"], Annotated[float, "amount"]]
"""

from __future__ import annotations

from typing import Annotated


def Field(name: str, typ):
    """An ordered, named record field."""

    return Annotated[typ, name]


class _RecordBuilder:
    def __getitem__(self, items):
        if not isinstance(items, tuple) or not items:
            raise TypeError(
                "Record[...] needs at least one ('name', type) pair"
            )
        # A single pair arrives as a flat 2-tuple; multiple pairs arrive
        # as a tuple of 2-tuples. Normalise to a list of pairs.
        if items and isinstance(items[0], tuple):
            pairs = list(items)
        else:
            pairs = [items]
        annotated = tuple(Field(name, typ) for name, typ in pairs)
        return tuple[annotated]  # tuple[Annotated[...], Annotated[...], ...]


Record = _RecordBuilder()
