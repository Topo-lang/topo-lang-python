"""Read .topo back into a Graph by parsing it with the real toolchain.

Round-trip fidelity is the decisive constraint for topo-app. To prove it
honestly, read-back must go through the *actual* Topo parser, not a
Python re-implementation of the grammar (which could agree with the
emitter by accident). We invoke ``topo --ast-dump`` and reconstruct the
graph from the parser's own structured dump. This simultaneously proves
"emitted .topo parses under the merged grammar" (the dump only succeeds
if the parser accepts it) and yields graph' for the equivalence check.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ._graph import Edge, Flow, Graph, TypeRef
from ._toolchain import topo_bin

_HANDLER_RE = re.compile(r"HandlerDecl '(\w+)\((.*?)\)\s*->\s*(.+)'")
_FLOW_RE = re.compile(r"FlowBlock '(\w+)'")
_EDGE_RE = re.compile(r"Edge (\w+) -> (\w+)(?:\s*\[terminal\])?")
_NS_RE = re.compile(r"NamespaceDecl '(\w+)'")


def _parse_type(spec: str) -> TypeRef:
    spec = spec.strip()
    m = re.match(r"record<(.+)>$", spec)
    if m:
        fields = []
        # Split top-level "name: type" pairs. Record fields here are
        # scalar-typed (this proof of concept's record nesting is one
        # level, matching the canonical order example), so a comma split
        # is sufficient.
        for part in m.group(1).split(","):
            name, _, ftype = part.partition(":")
            fields.append((name.strip(), TypeRef(scalar=ftype.strip())))
        return TypeRef(record=tuple(fields))
    return TypeRef(scalar=spec)


def read_topo(text: str) -> Graph:
    """Parse `.topo` source text into a Graph via ``topo --ast-dump``.

    Raises CalledProcessError if the toolchain rejects the source, which
    is itself the grammar-conformance signal.
    """

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "roundtrip.topo"
        p.write_text(text, encoding="utf-8")
        proc = subprocess.run(
            [str(topo_bin()), "--ast-dump", str(p)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            "topo --ast-dump",
            output=proc.stdout,
            stderr=proc.stderr,
        )

    namespace = ""
    handlers = []
    flow = None
    for line in proc.stdout.splitlines():
        s = line.strip()
        m = _NS_RE.match(s)
        if m:
            namespace = m.group(1)
            continue
        m = _HANDLER_RE.match(s)
        if m:
            name, params, ret = m.group(1), m.group(2).strip(), m.group(3)
            in_type = None
            if params:
                # "Type in" — strip the conventional parameter name.
                type_spec = params.rsplit(" ", 1)[0]
                in_type = _parse_type(type_spec)
            handlers.append(_make_handler(name, in_type, _parse_type(ret)))
            continue
        m = _FLOW_RE.match(s)
        if m:
            flow = Flow(name=m.group(1))
            continue
        m = _EDGE_RE.match(s)
        if m and flow is not None:
            src, tgt = m.group(1), m.group(2)
            flow.edges.append(Edge(src, None if tgt == "void" else tgt))

    g = Graph(namespace=namespace, handlers=handlers, flow=flow)
    return g


def _make_handler(name, in_type, out_type):
    from ._graph import Handler

    return Handler(name=name, in_type=in_type, out_type=out_type)
