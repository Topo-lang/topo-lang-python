"""A minimal topo-app program: a 3-stage order flow written in idiomatic
Python. No ``.topo`` is written by hand anywhere; the framework derives
it from the handler signatures and the declared flow.

Run as a script to see the snapshot, the emitted .topo, and the
round-trip verdict.
"""

import topo

app = topo.App("orders")

# In/Out are read from annotations — the signature is the contract
# (handler is a pure Functor; never declared twice, once in code and
# once by hand). `Record[...]` spells a stdlib record<...> with
# explicit, ordered fields.
OrderRec = topo.Record[("id", int), ("amount", float)]


@app.handler
def parse(raw: str) -> OrderRec:
    parts = raw.split(":")
    return (int(parts[0]), float(parts[1]))


@app.handler
def validate(order: OrderRec) -> OrderRec:
    oid, amount = order
    return (oid, amount if amount > 0 else 0.0)


@app.handler
def persist(order: OrderRec) -> bool:
    return order[1] > 0.0


# A linear logic chain: parse -> validate -> persist -> void.
app.flow("order_pipeline", parse, validate, persist)


def main() -> None:
    import json

    cfg = topo.config(app)
    print("=== snapshot (whole graph, one place) ===")
    print(json.dumps(cfg.snapshot(), indent=2))
    print("\n=== emitted .topo (round-trippable contract) ===")
    print(cfg.emit_topo())
    g2 = cfg.roundtrip()
    print("=== round-trip: graph == graph' ===")
    print(app.graph.equivalent_to(g2))


if __name__ == "__main__":
    main()
