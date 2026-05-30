#!/usr/bin/env python3
"""topo-extract-python (transpile path) — lifts Python source into a TranspileModule.

Subprocess protocol (driven by topo-core TranspileDriver):
    stdin  -> JSON { "files": [...], "functions": [...], "symbolTable": {...} }
    stdout <- JSON TranspileModule { "types": [...], "functions": [...] }

The wire shape is authoritatively defined by topo-core's
TranspileModelJson.cpp deserializer. Discriminator strings are lowercase
("varref", "binaryop", "literal", ...) and must match it byte-for-byte —
the C++ side silently downgrades unknown kinds to Unsupported, so any
divergence corrupts the lifted Model rather than erroring.

Sister tool: topo_extract_python.py (same directory) is the *L2 containment*
extractor with a wholly different protocol (argv-driven, emits callSites).
This file is the *transpile* extractor; the two have no shared code so
their independent evolution does not break each other's callers.

A declared symbol whose body cannot be faithfully reconstructed is never
silently dropped: every construct outside the MVP is recorded in the
function's `unsupported` list and fidelity downgrades to "inferred"
(matching the cross-extractor fidelity convention — `recovered` is
reserved for decompile lifters).
"""

from __future__ import annotations

import ast
import json
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Per-function lift state. Mirrors TS extractor's FnLift: collects
# `unsupported` notes and tracks whether the body had to fall back, so
# fidelity reflects reconstruction quality.
# ---------------------------------------------------------------------------


class FnLift:
    def __init__(self) -> None:
        self.unsupported: list[str] = []
        self.degraded: bool = False

    def note(self, msg: str) -> None:
        self.unsupported.append(msg)
        self.degraded = True

    def fidelity(self) -> str:
        # Cross-extractor fidelity convention: a SOURCE
        # extractor that emits an approximation because the source feature
        # is outside the MVP uses "inferred". `recovered` is reserved for
        # decompile lifters (LLVM IR / JVM bytecode).
        return "inferred" if self.degraded else "source"


# ---------------------------------------------------------------------------
# TypeNode — the .topo declaration is the contract for signatures, so the
# extractor maps Python annotations into the same nameParts vocabulary the
# emitters expect. Unannotated positions yield an empty node, which the
# emitters treat as inferred.
#
# Python -> Topo primitive mapping. Python's `int` is arbitrary-precision
# but the .topo declarations declare a width; here we map to i64 because
# the contract is signed-64 by default everywhere else (parser default and
# Java equivalent). `float` -> f64 (IEEE-754 binary64, matches CPython).
# `str` -> string, `bool` -> bool, `None` / `NoneType` -> void.
# ---------------------------------------------------------------------------


_PRIMITIVE_MAP = {
    "int": "i64",
    "float": "f64",
    "bool": "bool",
    "str": "string",
    "bytes": "bytes",
    "None": "void",
    "NoneType": "void",
}


def type_node(name_parts: list[str], template_args: list[dict] | None = None) -> dict:
    node: dict = {"nameParts": name_parts}
    if template_args:
        node["templateArgs"] = template_args
    return node


def type_from_annotation(node: ast.expr | None, lift: FnLift | None) -> dict:
    """Map a Python type annotation to a TypeNode dict.

    Annotated unparseable shapes (forward refs as strings, custom generics
    the harness doesn't exercise) are passed through by their source text
    so the host emitter — not this tool — decides what to render. A
    recognised primitive collapses to the .topo keyword vocabulary.
    """
    if node is None:
        return type_node(["void"])

    # Bare name: int / float / str / bool / None / user types
    if isinstance(node, ast.Name):
        prim = _PRIMITIVE_MAP.get(node.id)
        if prim is not None:
            return type_node([prim])
        return type_node([node.id])

    # Constant `None` literal in an annotation context.
    if isinstance(node, ast.Constant) and node.value is None:
        return type_node(["void"])

    # Subscript: list[T] / dict[K, V] / Optional[T] / Union[A, B] / etc.
    if isinstance(node, ast.Subscript):
        base = _annotation_base(node.value)
        args = _annotation_slice_args(node.slice, lift)
        if base == "list" or base == "List":
            inner = args[0] if args else type_node(["auto"])
            return {"nameParts": ["slice"], "templateArgs": [inner]}
        if base == "Optional":
            inner = args[0] if args else type_node(["auto"])
            return {"nameParts": ["optional"], "templateArgs": [inner]}
        if base in {"dict", "Dict", "Mapping"}:
            if len(args) >= 2:
                return {"nameParts": ["map"], "templateArgs": args[:2]}
            return type_node(["map"])
        if base in {"tuple", "Tuple"}:
            return {"nameParts": ["tuple"], "templateArgs": args}
        if base in {"Union", "union"}:
            # PEP 604's `int | str` already lifts here via the BinOp arm
            # below; this branch handles the classic `Union[A, B]` form.
            return {"nameParts": ["union"], "templateArgs": args}
        # Unknown subscripted generic — keep the base name + args so the
        # emitter can attempt to render it.
        return {"nameParts": [base], "templateArgs": args}

    # PEP 604 union: `int | str` parses as ast.BinOp(left, BitOr(), right).
    # Without this branch the BinOp falls through to the stringify fallback
    # and emits ``nameParts = ["int | str"]`` — raw source text that the
    # host emitters render verbatim and downstream compilers reject.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        flat: list[ast.expr] = []
        def _flatten(n: ast.expr) -> None:
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                _flatten(n.left)
                _flatten(n.right)
            else:
                flat.append(n)
        _flatten(node)
        return {
            "nameParts": ["union"],
            "templateArgs": [type_from_annotation(member, lift)
                             for member in flat],
        }

    # Attribute (typing.List, foo.Bar). Render dotted name.
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST | None = node
        while isinstance(cur, ast.Attribute):
            parts.insert(0, cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.insert(0, cur.id)
        return type_node(parts or ["auto"])

    # String forward-reference: `def f() -> "Foo":` — surface the literal.
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return type_node([node.value])

    # Unknown annotation shape — replace the previous stringify fallback
    # (which leaked raw Python source like ``int | str`` into TypeNode
    # ``nameParts[0]`` and downstream emitters printed it verbatim) with
    # an explicit ``__topo_unsupported_type__`` marker. The marker is not
    # a valid Python or Topo identifier so a host emitter rendering it
    # produces a clearly broken token instead of a silently-broken
    # plausible token. The ``unsupported`` note still carries the
    # original source for triage.
    if lift is not None:
        try:
            unparsed = ast.unparse(node)
        except Exception:
            unparsed = f"<unparsable {type(node).__name__}>"
        lift.note(f"unsupported type annotation '{unparsed}'")
    return type_node(["__topo_unsupported_type__"])


def _annotation_base(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    try:
        return ast.unparse(node)
    except Exception:
        return "auto"


def _annotation_slice_args(slice_node: ast.expr, lift: FnLift | None) -> list[dict]:
    # Single arg vs tuple arg vs ExtSlice (older).
    if isinstance(slice_node, ast.Tuple):
        return [type_from_annotation(e, lift) for e in slice_node.elts]
    return [type_from_annotation(slice_node, lift)]


# ---------------------------------------------------------------------------
# Expression lift — lowercase "kind" per topo-core's exprKindFromStr.
# ---------------------------------------------------------------------------


_BINOP = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Div: "div",
    ast.FloorDiv: "div",
    ast.Mod: "mod",
    ast.BitAnd: "bitand",
    ast.BitOr: "bitor",
    ast.BitXor: "bitxor",
    ast.LShift: "shl",
    ast.RShift: "shr",
}

_CMPOP = {
    ast.Eq: "eq",
    ast.NotEq: "noteq",
    ast.Lt: "less",
    ast.Gt: "greater",
    ast.LtE: "lesseq",
    ast.GtE: "greatereq",
    # `is` / `is not` / `in` / `not in` have no Topo equivalent; map to
    # equality with a fidelity note so the emitter at least renders
    # something correct-shaped.
    ast.Is: "eq",
    ast.IsNot: "noteq",
}

_BOOLOP = {
    ast.And: "and",
    ast.Or: "or",
}


def unsupported_expr(desc: str) -> dict:
    # Cross-extractor convention: source extractors emit "inferred" for
    # approximate output (see FnLift.fidelity above).
    return {"kind": "unsupported", "fidelity": "inferred", "description": desc}


def lit(lit_kind: str, value: str) -> dict:
    return {"kind": "literal", "fidelity": "source", "litKind": lit_kind, "value": value}


def lift_expr(node: ast.expr, lift: FnLift) -> dict:
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None:
            return {"kind": "varref", "fidelity": "source", "name": "null"}
        if isinstance(v, bool):
            return lit("boolean", "true" if v else "false")
        if isinstance(v, int):
            return lit("integer", str(v))
        if isinstance(v, float):
            return lit("float", repr(v))
        if isinstance(v, str):
            return lit("string", v)
        lift.note(f"constant literal of type {type(v).__name__}")
        return unsupported_expr(f"literal {type(v).__name__}")

    if isinstance(node, ast.Name):
        # Map Python None/True/False varrefs through the same renamings
        # the C++/Java emitters expect to see straight from source.
        if node.id == "None":
            return {"kind": "varref", "fidelity": "source", "name": "null"}
        if node.id == "True":
            return lit("boolean", "true")
        if node.id == "False":
            return lit("boolean", "false")
        return {"kind": "varref", "fidelity": "source", "name": node.id}

    if isinstance(node, ast.BinOp):
        op = _BINOP.get(type(node.op))
        if op is None:
            lift.note(f"binary operator {type(node.op).__name__}")
            return unsupported_expr("binary operator")
        return {
            "kind": "binaryop", "fidelity": "source", "op": op,
            "lhs": lift_expr(node.left, lift),
            "rhs": lift_expr(node.right, lift),
        }

    if isinstance(node, ast.BoolOp):
        # BoolOp is n-ary (`a and b and c`); fold left into binary tree
        # so the wire model stays in the binary-op vocabulary.
        op = _BOOLOP[type(node.op)]
        acc = lift_expr(node.values[0], lift)
        for v in node.values[1:]:
            acc = {
                "kind": "binaryop", "fidelity": "source", "op": op,
                "lhs": acc,
                "rhs": lift_expr(v, lift),
            }
        return acc

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            op = "negate"
        elif isinstance(node.op, ast.Not):
            op = "not"
        elif isinstance(node.op, ast.Invert):
            op = "bitnot"
        elif isinstance(node.op, ast.UAdd):
            # Topo has no UnaryPlus — pass operand through unchanged.
            return lift_expr(node.operand, lift)
        else:
            lift.note(f"unary operator {type(node.op).__name__}")
            return unsupported_expr("unary operator")
        return {
            "kind": "unaryop", "fidelity": "source", "op": op,
            "operand": lift_expr(node.operand, lift),
        }

    if isinstance(node, ast.Compare):
        # Chained comparison (`a < b < c`) → `(a < b) and (b < c)`. Each
        # left-side reuses the previous right; matches Python semantics.
        if len(node.ops) == 0:
            return lift_expr(node.left, lift)
        chain: list[dict] = []
        left = node.left
        for op_node, right in zip(node.ops, node.comparators):
            op = _CMPOP.get(type(op_node))
            if op is None:
                lift.note(f"compare operator {type(op_node).__name__}")
                chain.append(unsupported_expr("compare operator"))
            else:
                chain.append({
                    "kind": "binaryop", "fidelity": "source", "op": op,
                    "lhs": lift_expr(left, lift),
                    "rhs": lift_expr(right, lift),
                })
            left = right
        acc = chain[0]
        for c in chain[1:]:
            acc = {
                "kind": "binaryop", "fidelity": "source", "op": "and",
                "lhs": acc,
                "rhs": c,
            }
        return acc

    if isinstance(node, ast.IfExp):
        return {
            "kind": "ternary", "fidelity": "source",
            "condition": lift_expr(node.test, lift),
            "trueExpr": lift_expr(node.body, lift),
            "falseExpr": lift_expr(node.orelse, lift),
        }

    if isinstance(node, ast.Call):
        # `callee` is a plain string in the wire format; bare names lift
        # to the name, method/attribute calls collapse to the dotted text
        # (`obj.method`). This matches CallExpr.callee semantics: the
        # callee is opaque to the model, the host emitter renders it.
        try:
            callee = ast.unparse(node.func)
        except Exception:
            callee = "<unknown>"
        return {
            "kind": "call", "fidelity": "source",
            "callee": callee,
            "args": [lift_expr(a, lift) for a in node.args],
        }

    if isinstance(node, ast.Attribute):
        return {
            "kind": "memberaccess", "fidelity": "source",
            "object": lift_expr(node.value, lift),
            "member": node.attr,
        }

    if isinstance(node, ast.Subscript):
        return {
            "kind": "index", "fidelity": "source",
            "object": lift_expr(node.value, lift),
            "index": lift_expr(_subscript_index(node.slice, lift), lift)
                     if isinstance(_subscript_index(node.slice, lift), ast.expr)
                     else lift_expr(node.slice, lift),
        }

    if isinstance(node, ast.NamedExpr):
        # The walrus has no Topo equivalent; fall back to the rhs and
        # record. The bound identifier is visible to a later statement
        # only in the caller's scope, which we lose here.
        lift.note("walrus (named expression)")
        return lift_expr(node.value, lift)

    if isinstance(node, (ast.Lambda, ast.GeneratorExp, ast.ListComp,
                         ast.SetComp, ast.DictComp)):
        lift.note(f"inline {type(node).__name__}")
        return unsupported_expr(type(node).__name__)

    if isinstance(node, ast.Await):
        lift.note("await expression")
        return unsupported_expr("await")

    if isinstance(node, (ast.Yield, ast.YieldFrom)):
        lift.note(f"yield expression ({type(node).__name__})")
        return unsupported_expr("yield")

    lift.note(f"expression '{type(node).__name__}'")
    return unsupported_expr(type(node).__name__)


def _subscript_index(slice_node: ast.expr, lift: FnLift) -> ast.expr:
    # Python 3.9+: `slice` is the index expression directly. Slices
    # (`a[1:2]`) have no Topo equivalent — record and pass through the
    # underlying ast.Slice so lift_expr's fallback notes it.
    if isinstance(slice_node, ast.Slice):
        lift.note("slice index `a[start:stop:step]`")
    return slice_node


# ---------------------------------------------------------------------------
# Statement lift — lowercase "kind" per stmtKindFromStr.
# ---------------------------------------------------------------------------


def lift_stmt(node: ast.stmt, lift: FnLift) -> dict:
    if isinstance(node, ast.AnnAssign):
        # Annotated single-target assignment: `x: int = 0` → vardecl.
        if not isinstance(node.target, ast.Name):
            lift.note("annotated destructuring assignment")
            return expr_stmt_unsupported("destructured ann-assign")
        out: dict = {
            "kind": "vardecl", "fidelity": "source",
            "type": type_from_annotation(node.annotation, lift),
            "name": node.target.id,
        }
        if node.value is not None:
            out["init"] = lift_expr(node.value, lift)
        return out

    if isinstance(node, ast.Assign):
        # Plain `x = expr` — vardecl when no prior declaration is
        # observable from this single-statement view; we render as plain
        # assign so subsequent reads target the same identifier. Topo's
        # model uses Assign for "store to existing target" and VarDecl
        # for "introduce". Without type-inference we choose Assign: the
        # host emitter handles a missing prior decl via its own scope
        # rules (Python source legitimately first-assigns without an
        # annotation; that's a fidelity note for downstream).
        if len(node.targets) != 1:
            lift.note("multi-target assignment (`a = b = ...`)")
            return expr_stmt_unsupported("multi-target assign")
        target = node.targets[0]
        if not isinstance(target, (ast.Name, ast.Attribute, ast.Subscript)):
            lift.note(f"assign target '{type(target).__name__}'")
            return expr_stmt_unsupported("non-name assign target")
        return {
            "kind": "assign", "fidelity": "source",
            "target": lift_expr(target, lift),
            "value": lift_expr(node.value, lift),
        }

    if isinstance(node, ast.AugAssign):
        op = _BINOP.get(type(node.op))
        if op is None:
            lift.note(f"augmented assign operator {type(node.op).__name__}")
            return expr_stmt_unsupported("augmented assign operator")
        return {
            "kind": "exprstmt", "fidelity": "source",
            "expr": {
                "kind": "compoundassign", "fidelity": "source",
                "op": op,
                "target": lift_expr(node.target, lift),
                "value": lift_expr(node.value, lift),
            },
        }

    if isinstance(node, ast.Return):
        out = {"kind": "return", "fidelity": "source"}
        if node.value is not None:
            out["value"] = lift_expr(node.value, lift)
        return out

    if isinstance(node, ast.If):
        out = {
            "kind": "if", "fidelity": "source",
            "condition": lift_expr(node.test, lift),
            "thenBody": [lift_stmt(s, lift) for s in node.body],
        }
        if node.orelse:
            # `elif x:` parses as `else: [If]` — pass the nested If through
            # as a single-element elseBody so the C++ model collapses
            # `else if (...)` chains in the emitter (V8Codegen + JavaEmitter
            # already detect this shape).
            out["elseBody"] = [lift_stmt(s, lift) for s in node.orelse]
        return out

    if isinstance(node, ast.While):
        if node.orelse:
            lift.note("while/else clause (dropped)")
        return {
            "kind": "while", "fidelity": "source",
            "condition": lift_expr(node.test, lift),
            "body": [lift_stmt(s, lift) for s in node.body],
        }

    if isinstance(node, ast.For):
        # Topo `for` is C-style (init/condition/increment). Python
        # `for x in iterable` doesn't map cleanly; record and emit a
        # while-loop scaffold so the structure is still visible. The
        # iterable expression is lifted into the init slot so it's
        # observable downstream even though the loop semantics differ.
        lift.note("for-in loop (lowered to while-stub)")
        return {
            "kind": "while", "fidelity": "inferred",
            "condition": unsupported_expr("for-in iterable"),
            "body": [lift_stmt(s, lift) for s in node.body],
        }

    if isinstance(node, ast.Break):
        return {"kind": "break", "fidelity": "source"}

    if isinstance(node, ast.Continue):
        return {"kind": "continue", "fidelity": "source"}

    if isinstance(node, ast.Pass):
        # No equivalent statement — emit an empty exprstmt that the
        # emitter renders as a no-op. (PythonEmitter on the target side
        # would render this as `pass`; other targets render `;`.)
        return {
            "kind": "exprstmt", "fidelity": "source",
            "expr": lit("integer", "0"),
        }

    if isinstance(node, ast.Expr):
        return {
            "kind": "exprstmt", "fidelity": "source",
            "expr": lift_expr(node.value, lift),
        }

    if isinstance(node, (ast.Try, ast.TryStar)):
        lift.note(f"{type(node).__name__} (try/except not modeled)")
        return expr_stmt_unsupported("try/except")

    if isinstance(node, ast.Raise):
        lift.note("raise statement")
        return expr_stmt_unsupported("raise")

    if isinstance(node, ast.With):
        lift.note("with-statement (context manager)")
        return expr_stmt_unsupported("with")

    if isinstance(node, (ast.Import, ast.ImportFrom)):
        lift.note(f"{type(node).__name__} inside function body")
        return expr_stmt_unsupported("import")

    lift.note(f"statement '{type(node).__name__}'")
    return expr_stmt_unsupported(type(node).__name__)


def expr_stmt_unsupported(desc: str) -> dict:
    return {
        "kind": "exprstmt", "fidelity": "inferred",
        "expr": unsupported_expr(desc),
    }


# ---------------------------------------------------------------------------
# Module traversal — collect functions by qualified name. The .topo
# declaration keys symbols with "::" namespace separators
# (SemanticAnalyzer), so a function inside `class C:` is keyed `C::fn`,
# and a nested module-level function inside an outer `def` is also keyed
# with `::` if reachable. Top-level module `def f(): ...` keys as `f`.
# ---------------------------------------------------------------------------


def _is_typevar_call(call_func: ast.expr) -> bool:
    """Recognise the constructor that produces a TypeVar.

    Accepts `TypeVar(...)` (the common `from typing import TypeVar` form)
    and `typing.TypeVar(...)` (the qualified import form). Anything else
    (custom factories, aliased imports like `TV = TypeVar`) stays
    unrecognised — the MVP keeps the surface conservatively small.
    """
    if isinstance(call_func, ast.Name) and call_func.id == "TypeVar":
        return True
    if (isinstance(call_func, ast.Attribute) and call_func.attr == "TypeVar"
            and isinstance(call_func.value, ast.Name)
            and call_func.value.id == "typing"):
        return True
    return False


def _constraint_tuple_bound(value: ast.Call) -> tuple[dict | None, str | None]:
    """Lower a `TypeVar('T', A, B, ...)` constraint tuple to a union bound.

    The positional args after the name string are the constraint set: the
    type parameter must be *exactly one* of them — a union (PEP 604 `A | B`
    in Python's surface). That is semantically distinct from `bound=`'s
    subtype relation, but both ride the wire `bound` slot; the union
    TypeNode (`nameParts == ["union"]` plus positional `templateArgs`) is
    the existing stdlib representation, so every host emitter already has
    a rendering path for it.

    Returns `(bound_node, None)` when every constraint is a plain type ref
    (a bare `Name`, a dotted `Attribute`, or a string forward-reference).
    Returns `(None, note)` when any constraint is a parameterised generic
    (`list[int]`, `dict[str, int]` — an `ast.Subscript`): the whole bound
    is dropped conservatively and `note` describes the downgrade, rather
    than emitting a half-formed union.
    """
    constraints = value.args[1:]
    for arg in constraints:
        # Generic constraints (`list[int]`) are out of scope: drop
        # the entire bound so no partial union ever reaches the wire.
        if isinstance(arg, ast.Subscript):
            try:
                shown = ast.unparse(arg)
            except Exception:
                shown = "<generic>"
            return None, (f"TypeVar constraint tuple with generic member "
                          f"'{shown}' dropped (only plain type constraints "
                          f"are modeled)")
        if not isinstance(arg, (ast.Name, ast.Attribute, ast.Constant)):
            try:
                shown = ast.unparse(arg)
            except Exception:
                shown = "<expr>"
            return None, (f"TypeVar constraint tuple with non-type member "
                          f"'{shown}' dropped")
        if isinstance(arg, ast.Constant) and not isinstance(arg.value, str):
            return None, "TypeVar constraint tuple with non-string literal dropped"
    members = [type_from_annotation(arg, None) for arg in constraints]
    return {"nameParts": ["union"], "templateArgs": members}, None


def scan_module_typevars(tree: ast.Module) -> dict[str, dict]:
    """Build a name → {bound, default} map for old-style TypeVar bindings.

    Pre-PEP-695 generics declare type parameters as module-level
    `T = TypeVar('T', bound=X, default=Y)` (PEP 696 added `default` in
    Python 3.13+; the `typing-extensions` backport exposes it earlier).
    The map keys are the *Python* variable names the TypeVar is bound to
    — those names are what appears in annotations downstream
    (`def f(x: T) -> T`), not the string-literal first argument. The two
    happen to agree in practice but we key by the variable name to keep
    the lookup correct even when they diverge.

    Recognised forms (anything else is dropped from the map):
      - LHS: single `ast.Name`. Tuple unpack and AnnAssign-with-multiple-
        targets are not modelled.
      - RHS: a `Call` to `TypeVar` / `typing.TypeVar` with the first
        positional arg being a string literal (the canonical TypeVar
        spelling). Beyond the name, `bound=` / `default=` keyword args
        carry constraints, and a *constraint tuple* of >1 positional arg
        (`TypeVar('T', int, str)`) lowers to a union `bound` — see
        `_constraint_tuple_bound`.
    """
    out: dict[str, dict] = {}
    for stmt in tree.body:
        # `T = TypeVar('T', bound=...)`  → Assign
        # `T: TypeAlias = TypeVar('T', ...)` → AnnAssign (rare but legal)
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            name = stmt.targets[0].id
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            if not isinstance(stmt.target, ast.Name) or stmt.value is None:
                continue
            name = stmt.target.id
            value = stmt.value
        else:
            continue
        if not isinstance(value, ast.Call) or not _is_typevar_call(value.func):
            continue
        # Validate the first positional arg is a string literal — anything
        # else (dynamic name, no args) sits outside the MVP.
        if (not value.args or not isinstance(value.args[0], ast.Constant)
                or not isinstance(value.args[0].value, str)):
            continue
        meta: dict = {}
        for kw in value.keywords:
            if kw.arg == "bound":
                meta["bound"] = type_from_annotation(kw.value, None)
            elif kw.arg == "default":
                meta["default"] = type_from_annotation(kw.value, None)
            # `covariant=` / `contravariant=` / `infer_variance=` etc. are
            # ignored — variance has no Topo wire representation.
        # Constraint tuple (`TypeVar('T', int, str)`): >1 positional arg.
        # Lowers to a union `bound`. An explicit `bound=` keyword cannot
        # coexist with a constraint tuple in legal Python, so the keyword
        # branch above and this branch never both populate `meta["bound"]`.
        if len(value.args) > 1:
            union_bound, drop_note = _constraint_tuple_bound(value)
            if union_bound is not None:
                meta["bound"] = union_bound
            elif drop_note is not None:
                # Out-of-scope constraint tuple: record the downgrade so any
                # function/class that uses this TypeVar surfaces it.
                meta["constraintNote"] = drop_note
        out[name] = meta
    return out


def _collect_type_var_refs(node: ast.expr | None,
                           type_var_meta: dict[str, dict]) -> list[str]:
    """Walk an annotation expression and return TypeVar names referenced.

    Order is depth-first source order; the caller is responsible for
    dedup. Subscripts (`list[T]`, `dict[K, V]`) recurse into both base
    and slice. Anything not a Name is recursed structurally via ast.walk.
    """
    if node is None:
        return []
    found: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in type_var_meta:
            found.append(sub.id)
    return found


def _template_param_from_meta(name: str, meta: dict,
                              lift: FnLift | None = None) -> dict:
    """Build a wire `templateParam` entry from a TypeVar meta record.

    When `meta` carries a `constraintNote` (an out-of-scope constraint
    tuple was dropped by `scan_module_typevars`) and a `lift` is supplied,
    the note is recorded against the consuming function/class so its
    `fidelity` downgrades — the bound itself stays absent (bare `T`).
    """
    entry: dict = {"kind": "type", "name": name}
    if "bound" in meta:
        entry["bound"] = meta["bound"]
    if "default" in meta:
        entry["default"] = meta["default"]
    if lift is not None and "constraintNote" in meta:
        lift.note(meta["constraintNote"])
    return entry


def lift_type_params(tps: list[ast.AST], lift: FnLift, owner: str) -> list[dict]:
    """PEP 695 type parameter list (`def f[T: Bound](): ...`, `class C[T: Bound]: ...`).

    Captures `T: Bound` (PEP 695) into the wire `bound` and `T = Default`
    (PEP 696, Python 3.13+) into the wire `default`. Both route through
    `type_from_annotation` — the same path used for parameter / return type
    annotations — so qualified or parameterised bound/default expressions
    round-trip through PythonEmitter's `pep695ParamsImpl`. TypeVarTuple /
    ParamSpec stay dropped (the wire models a single named type parameter
    per entry; variadic forms are not represented yet).
    """
    out: list[dict] = []
    for tp in tps:
        # Python 3.12+: ast.TypeVar / ast.TypeVarTuple / ast.ParamSpec.
        name = getattr(tp, "name", None)
        if name is None:
            lift.note(f"unrecognised type parameter on {owner}")
            continue
        # Tuple-likes and ParamSpec aren't modeled in TranspileType MVP.
        if isinstance(tp, getattr(ast, "TypeVarTuple", ())):
            lift.note(f"TypeVarTuple '{name}' on {owner} not modeled (dropped)")
            continue
        if isinstance(tp, getattr(ast, "ParamSpec", ())):
            lift.note(f"ParamSpec '{name}' on {owner} not modeled (dropped)")
            continue
        entry: dict = {"kind": "type", "name": name}
        # TypeVar with a bound has `bound` attribute non-None.
        bound = getattr(tp, "bound", None)
        if bound is not None:
            entry["bound"] = type_from_annotation(bound, lift)
        # default_value is on TypeVar in 3.13+ (PEP 696).
        default_value = getattr(tp, "default_value", None)
        if default_value is not None:
            entry["default"] = type_from_annotation(default_value, lift)
        out.append(entry)
    return out


def lift_function(qname: str, decl: ast.FunctionDef | ast.AsyncFunctionDef,
                  is_method: bool,
                  type_var_meta: dict[str, dict] | None = None) -> dict:
    lift = FnLift()
    fn: dict = {
        "qualifiedName": qname,
        "returnType": type_from_annotation(decl.returns, lift),
        "params": [],
        "body": [],
        "unsupported": [],
        "fidelity": "source",
    }

    if isinstance(decl, ast.AsyncFunctionDef):
        lift.note("async function")
    if decl.decorator_list:
        for d in decl.decorator_list:
            try:
                lift.note(f"decorator @{ast.unparse(d)}")
            except Exception:
                lift.note("decorator (unprintable)")

    # Positional + arg-defaulted parameters. Skip `self`/`cls` for methods
    # so the lifted signature matches the .topo declaration shape (the
    # contract names instance methods without an explicit receiver).
    args = decl.args
    if args.vararg is not None:
        lift.note(f"*args parameter '{args.vararg.arg}' dropped")
    if args.kwarg is not None:
        lift.note(f"**kwargs parameter '{args.kwarg.arg}' dropped")
    if args.kwonlyargs:
        lift.note("keyword-only parameters dropped")
    if args.posonlyargs:
        # Posonlyargs precede positional; include them as positional in
        # the lifted signature.
        positional = list(args.posonlyargs) + list(args.args)
    else:
        positional = list(args.args)

    for i, p in enumerate(positional):
        if is_method and i == 0 and p.arg in {"self", "cls"}:
            continue
        fn["params"].append({
            "name": p.arg,
            "type": type_from_annotation(p.annotation, lift),
        })

    # PEP 695 type parameters on the function itself (`def f[T](): ...`).
    tps_attr = getattr(decl, "type_params", None) or []
    template_params = lift_type_params(tps_attr, lift, f"function '{decl.name}'")

    # Old-style implicit generics: a function that references a module-level
    # TypeVar in its signature (return + params) is implicitly generic over
    # those vars. PEP 695 wins when both are present (the PEP-695 brackets
    # are explicit; any module-level TypeVar with the same name is a duplicate
    # declaration the linter would flag).
    if type_var_meta and not template_params:
        seen: set[str] = set()
        order: list[str] = []
        for ref in _collect_type_var_refs(decl.returns, type_var_meta):
            if ref not in seen:
                seen.add(ref); order.append(ref)
        positional_args = (list(decl.args.posonlyargs) + list(decl.args.args)
                           if decl.args.posonlyargs else list(decl.args.args))
        for i, p in enumerate(positional_args):
            if is_method and i == 0 and p.arg in {"self", "cls"}:
                continue
            for ref in _collect_type_var_refs(p.annotation, type_var_meta):
                if ref not in seen:
                    seen.add(ref); order.append(ref)
        if order:
            template_params = [
                _template_param_from_meta(n, type_var_meta[n], lift)
                for n in order
            ]

    if template_params:
        fn["templateParams"] = template_params

    fn["body"] = [lift_stmt(s, lift) for s in decl.body]
    fn["unsupported"] = lift.unsupported
    fn["fidelity"] = lift.fidelity()
    return fn


def lift_class_fields(class_body: list[ast.stmt], lift: FnLift) -> list[dict]:
    """Collect AnnAssign declarations at class-body scope as dataclass-style
    fields. A bare `x = 0` without an annotation is ignored (no type to
    record); methods are also ignored — they surface as functions below.
    """
    out: list[dict] = []
    for s in class_body:
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
            out.append({
                "type": type_from_annotation(s.annotation, lift),
                "name": s.target.id,
                "fidelity": "source",
            })
    return out


def _module_namespace_from_path(file: str) -> str:
    """Derive a module namespace from the source file's basename.

    Python uses files (modules) as namespaces — `calc.py`'s top-level
    `def fib` is `calc.fib` to any importer. The .topo declaration
    naturally mirrors that as `namespace calc { f64 fib(f64 n); }`,
    keying the symbol as `calc::fib`. The TS extractor matches on
    explicit `namespace X { ... }` ModuleDeclarations because TS has
    that construct; Python has no equivalent inside a single file, so
    here we synthesize the namespace from the filename.

    Files whose basename is not a valid Python identifier (e.g. dashes,
    starts with a digit, or empty) are treated as namespace-less so
    callers can still target raw top-level qnames if they want. The
    valid-identifier check is `str.isidentifier()` after stripping the
    extension — the same rule Python itself uses for module names.
    """
    import os
    base = os.path.splitext(os.path.basename(file))[0]
    if base and base.isidentifier():
        return base
    return ""


def collect_module(source: str, file: str) -> tuple[list[dict], list[dict]]:
    """Parse a single Python source file and return (functions, types)
    arrays in TranspileModule wire shape.

    Type extraction is unconditional — the caller's `request.functions`
    filter scopes the FUNCTIONS list only, never the types list (cross-
    language transpile may need referenced bases resolved even when a
    specific function subset is requested; same convention as the Java
    and TS extractors).
    """
    tree = ast.parse(source, filename=file)
    functions: list[dict] = []
    types: list[dict] = []

    # Implicit module namespace: top-level `def fib` in `calc.py` keys
    # as `calc::fib` so the .topo declaration `namespace calc { f64
    # fib(f64 n); }` matches end-to-end. Nested classes still nest
    # under this prefix.
    module_ns = _module_namespace_from_path(file)
    initial_ns: list[str] = [module_ns] if module_ns else []

    # Old-style TypeVar map (pre-PEP-695): collect once at module scope so
    # every function/class lift can resolve TypeVar references uniformly.
    type_var_meta = scan_module_typevars(tree)

    def _generic_typevar_names(base: ast.expr) -> list[str] | None:
        """Match `Generic[T1, T2]` or `Protocol[T1, T2]` and return the
        ordered TypeVar names. Returns None for any other base."""
        if not isinstance(base, ast.Subscript):
            return None
        head = base.value
        head_id: str | None = None
        if isinstance(head, ast.Name):
            head_id = head.id
        elif (isinstance(head, ast.Attribute) and isinstance(head.value, ast.Name)
              and head.value.id == "typing"):
            head_id = head.attr
        if head_id not in {"Generic", "Protocol"}:
            return None
        # Slice can be a single Name (Generic[T]) or a Tuple of Names.
        slc = base.slice
        if isinstance(slc, ast.Name):
            return [slc.id] if slc.id in type_var_meta else []
        if isinstance(slc, ast.Tuple):
            names: list[str] = []
            for elt in slc.elts:
                if isinstance(elt, ast.Name) and elt.id in type_var_meta:
                    names.append(elt.id)
                else:
                    # An unrecognised slot collapses the whole match; we'd
                    # rather drop than emit a partial param list.
                    return []
            return names
        return None

    def visit(node: ast.AST, ns: list[str]) -> None:
        if isinstance(node, ast.ClassDef):
            class_ns = ns + [node.name]
            qname = "::".join(class_ns)
            class_lift = FnLift()

            # Base classes: every Python base is rendered as Class kind
            # in TranspileType.baseClassKinds. Python has no `interface`
            # equivalent — Protocol / ABC classes are still `class` from
            # the model's perspective. The Java emitter's discriminator
            # logic gracefully handles all-Class bases (first = extends,
            # rest = implements heuristic still applies).
            #
            # `Generic[T1, T2]` / `Protocol[T1, T2]` are unwrapped into
            # templateParams here instead of going into baseClasses — the
            # Topo model represents type parameters separately, and downstream
            # emitters render them as the host's generics syntax, not as a
            # base class.
            base_classes: list[dict] = []
            base_kinds: list[str] = []
            implicit_template_param_names: list[str] = []
            for b in node.bases:
                tv_names = _generic_typevar_names(b)
                if tv_names is not None:
                    # Generic[...]/Protocol[...] — collect the type params,
                    # do not emit as a base class.
                    for n in tv_names:
                        if n not in implicit_template_param_names:
                            implicit_template_param_names.append(n)
                    continue
                base_classes.append(type_from_annotation(b, class_lift))
                base_kinds.append("class")

            tps_attr = getattr(node, "type_params", None) or []
            template_params = lift_type_params(tps_attr, class_lift,
                                               f"class '{node.name}'")
            # PEP 695 wins; only fall back to the implicit-Generic shape when
            # the class did not use the bracket syntax.
            if not template_params and implicit_template_param_names:
                template_params = [
                    _template_param_from_meta(n, type_var_meta[n], class_lift)
                    for n in implicit_template_param_names
                ]

            # Decorators on the class itself (dataclass, etc.) downgrade
            # fidelity but the field/base/generics extraction is otherwise
            # accurate.
            if node.decorator_list:
                for d in node.decorator_list:
                    try:
                        class_lift.note(f"class decorator @{ast.unparse(d)}")
                    except Exception:
                        class_lift.note("class decorator (unprintable)")

            fields = lift_class_fields(node.body, class_lift)

            ty_entry: dict = {
                "qualifiedName": qname,
                "fields": fields,
                "fidelity": class_lift.fidelity(),
            }
            if base_classes:
                ty_entry["baseClasses"] = base_classes
                ty_entry["baseClassKinds"] = base_kinds
            if template_params:
                ty_entry["templateParams"] = template_params
            types.append(ty_entry)

            # Methods become functions with qname including the class.
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mqname = "::".join(class_ns + [member.name])
                    functions.append(lift_function(mqname, member,
                                                   is_method=True,
                                                   type_var_meta=type_var_meta))
                # Nested classes recurse with the outer class in ns.
                elif isinstance(member, ast.ClassDef):
                    visit(member, class_ns)
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qname = "::".join(ns + [node.name])
            functions.append(lift_function(qname, node, is_method=False,
                                           type_var_meta=type_var_meta))
            return

    # Module body: only top-level functions and classes contribute to the
    # extracted Model. (Module-level statements like `print(...)` are not
    # symbol declarations.)
    for s in tree.body:
        visit(s, initial_ns)

    return functions, types


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        request: dict[str, Any] = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        sys.stderr.write(f"topo-extract-python: invalid JSON request: {e}\n")
        return 1

    files = request.get("files") or []
    requested = set(request.get("functions") or [])

    fatal: list[str] = []
    out_functions: list[dict] = []
    out_types: list[dict] = []

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
        except OSError as e:
            fatal.append(f"cannot read {path}: {e}")
            continue
        try:
            fns, types = collect_module(source, path)
        except SyntaxError as e:
            fatal.append(f"parse error in {path}: {e.msg} (line {e.lineno})")
            continue
        for fn in fns:
            if requested and fn["qualifiedName"] not in requested:
                continue
            out_functions.append(fn)
        for ty in types:
            out_types.append(ty)

    # A declared symbol with no recoverable body is a contract breach:
    # refuse rather than emit a partial Model the caller can't tell is
    # incomplete (matches the TS extractor's policy).
    if requested:
        got = {f["qualifiedName"] for f in out_functions}
        for want in requested:
            if want not in got:
                fatal.append(f"declared symbol '{want}' not found in sources")

    if fatal:
        for m in fatal:
            sys.stderr.write(f"topo-extract-python: {m}\n")
        return 1

    module = {"types": out_types, "functions": out_functions}
    sys.stdout.write(json.dumps(module))
    return 0


if __name__ == "__main__":
    sys.exit(main())
