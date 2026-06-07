#!/usr/bin/env python3
"""Smoke test for topo-extract-python (transpile path).

Drives the production tool through its real stdin → stdout protocol and
asserts the lifted TranspileModule uses the lowercase discriminator
vocabulary the topo-core deserializer expects, and that an unliftable
declared symbol fails loudly instead of producing a partial Model.

The shape of these assertions mirrors topo-lang-typescript's
smoke.test.mjs deliberately so the cross-extractor contract stays
symmetric — a regression in either language is easy to spot by diffing
the test pairs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "topo_extract_transpile_python.py")


def run(py_source: str, request_overrides: dict | None = None,
        module_name: str = "_smoke") -> dict:
    """Write py_source to a controlled-name .py file under a temp dir,
    build a request that points at it, run the extractor, return the
    parsed module.

    The Python extractor derives a module namespace from the source file's
    basename (so `calc.py`'s top-level `def fib` keys as `calc::fib`), so
    tests pin the basename explicitly via `module_name` to keep the
    expected qnames deterministic. Leading-underscore default avoids any
    accidental collision with real-world module names.
    """
    tmpdir = tempfile.mkdtemp(prefix="topo-extract-py-")
    path = os.path.join(tmpdir, module_name + ".py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(py_source)
        req = {"files": [path], "functions": [], "symbolTable": {}}
        if request_overrides:
            req.update(request_overrides)
        result = subprocess.run(
            [sys.executable, SCRIPT],
            input=json.dumps(req),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"extractor failed (exit {result.returncode}): {result.stderr}")
        return json.loads(result.stdout)
    finally:
        try:
            os.unlink(path)
        finally:
            os.rmdir(tmpdir)


def run_expect_fail(py_source: str, request_overrides: dict,
                    module_name: str = "_smoke") -> int:
    tmpdir = tempfile.mkdtemp(prefix="topo-extract-py-fail-")
    path = os.path.join(tmpdir, module_name + ".py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(py_source)
        req = {"files": [path], "functions": [], "symbolTable": {}}
        req.update(request_overrides)
        result = subprocess.run(
            [sys.executable, SCRIPT],
            input=json.dumps(req),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        return result.returncode
    finally:
        try:
            os.unlink(path)
        finally:
            os.rmdir(tmpdir)


# Convenience prefix the smoke tests prepend to expected qnames. All
# tests use the default `_smoke` module name, so a top-level `def add3`
# keys as `_smoke::add3`. A class `C` with method `f` keys as
# `_smoke::C::f`. This keeps the assertion noise local: the rule
# (basename-derived namespace) lives in the extractor; tests just thread
# the known prefix through.
NS = "_smoke::"


class SmokeTests(unittest.TestCase):
    # --- Functions: signatures + bodies -----------------------------------

    def test_lifts_function_with_arithmetic(self):
        mod = run("def add3(x: int) -> int:\n    return x + 3\n",
                  {"functions": [NS + "add3"]})
        self.assertEqual(len(mod["functions"]), 1)
        fn = mod["functions"][0]
        self.assertEqual(fn["qualifiedName"], NS + "add3")
        self.assertEqual(fn["fidelity"], "source")
        self.assertEqual(fn["returnType"]["nameParts"], ["i64"])
        self.assertEqual(fn["params"][0]["type"]["nameParts"], ["i64"])
        ret = fn["body"][0]
        self.assertEqual(ret["kind"], "return")
        self.assertEqual(ret["value"]["kind"], "binaryop")
        self.assertEqual(ret["value"]["op"], "add")
        self.assertEqual(ret["value"]["lhs"]["kind"], "varref")
        self.assertEqual(ret["value"]["rhs"]["kind"], "literal")
        self.assertEqual(ret["value"]["rhs"]["litKind"], "integer")

    def test_method_qualified_with_double_colon(self):
        # `class C: def f(self, n: int) -> int` keys as "<module>::C::f"
        # so the SemanticAnalyzer/topo declaration vocabulary matches.
        mod = run("class C:\n    def f(self, n: int) -> int:\n        return n\n",
                  {"functions": [NS + "C::f"]})
        self.assertEqual(len(mod["functions"]), 1)
        self.assertEqual(mod["functions"][0]["qualifiedName"], NS + "C::f")
        # self was stripped — params is just [n].
        self.assertEqual([p["name"] for p in mod["functions"][0]["params"]],
                         ["n"])

    def test_control_flow_if_while(self):
        mod = run(
            "def g(n: int) -> int:\n"
            "    s: int = 0\n"
            "    i: int = 1\n"
            "    while i <= n:\n"
            "        s = s + i\n"
            "        i = i + 1\n"
            "    if s > 10:\n"
            "        return s\n"
            "    else:\n"
            "        return 0\n",
            {"functions": [NS + "g"]})
        body = mod["functions"][0]["body"]
        self.assertEqual(body[0]["kind"], "vardecl")
        self.assertEqual(body[1]["kind"], "vardecl")
        self.assertEqual(body[2]["kind"], "while")
        self.assertEqual(body[3]["kind"], "if")
        self.assertIn("elseBody", body[3])

    def test_async_function_recorded_as_unsupported_inferred(self):
        mod = run("async def h() -> int:\n    return 1\n",
                  {"functions": [NS + "h"]})
        fn = mod["functions"][0]
        # Convention: SOURCE extractor's approximation tags as "inferred".
        self.assertEqual(fn["fidelity"], "inferred")
        self.assertTrue(
            any("async" in u for u in fn["unsupported"]),
            f"async note missing: {fn['unsupported']}")

    def test_missing_declared_symbol_fails_loudly(self):
        code = run_expect_fail(
            "def present() -> int:\n    return 0\n",
            {"functions": [NS + "absent"]})
        self.assertNotEqual(code, 0)

    # --- Generics ---------------------------------------------------------

    def test_pep695_function_generic(self):
        # PEP 695 syntax requires Python 3.12+. Skip on older runtimes.
        if sys.version_info < (3, 12):
            self.skipTest("PEP 695 syntax requires Python 3.12+")
        mod = run("def identity[T](x: T) -> T:\n    return x\n",
                  {"functions": [NS + "identity"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["templateParams"], [{"kind": "type", "name": "T"}])
        self.assertEqual(fn["fidelity"], "source")

    def test_pep695_single_bound_captures_bound_and_stays_source(self):
        if sys.version_info < (3, 12):
            self.skipTest("PEP 695 syntax requires Python 3.12+")
        # Single trait-bound MVP: `[T: int]` now lifts into the wire `bound`
        # field rather than dropping. `int` maps to `i64` (same mapping the
        # parameter / return type annotations use), keeping the bound
        # semantically consistent with the rest of the model.
        mod = run("def pick[T: int](x: T) -> T:\n    return x\n",
                  {"functions": [NS + "pick"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["templateParams"], [{
            "kind": "type",
            "name": "T",
            "bound": {"nameParts": ["i64"]},
        }])
        self.assertEqual(fn["fidelity"], "source")
        self.assertEqual(fn["unsupported"], [])

    def test_pep696_default_captures_default_and_stays_source(self):
        if sys.version_info < (3, 13):
            self.skipTest("PEP 696 type-parameter defaults require Python 3.13+")
        # PEP 696 default `[T = int]` on a function: the extractor now
        # captures the default into the wire `default` field (parallel to
        # `bound`), no longer dropping it. `int` maps to `i64` per the
        # Python→Topo annotation mapping used everywhere else.
        mod = run("def id[T = int](x: T) -> T:\n    return x\n",
                  {"functions": [NS + "id"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["templateParams"], [{
            "kind": "type",
            "name": "T",
            "default": {"nameParts": ["i64"]},
        }])
        self.assertEqual(fn["fidelity"], "source")
        self.assertEqual(fn["unsupported"], [])

    # --- Old-style TypeVar (pre-PEP-695) -------------------------------

    def test_old_style_typevar_in_function_signature_lifts_to_templateparams(self):
        # Pre-PEP-695: `T = TypeVar('T', bound=int)` at module scope plus a
        # function whose return / params reference T should now surface T
        # in templateParams with its bound from the module-level meta.
        mod = run(
            "from typing import TypeVar\n"
            "T = TypeVar('T', bound=int)\n"
            "def pick(x: T) -> T:\n    return x\n",
            {"functions": [NS + "pick"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["templateParams"], [{
            "kind": "type",
            "name": "T",
            "bound": {"nameParts": ["i64"]},
        }])

    def test_old_style_typevar_default_via_typing_qualified_call(self):
        # `typing.TypeVar('T', default=int)` is the qualified-import form;
        # the extractor recognises both `TypeVar(...)` and `typing.TypeVar(...)`.
        mod = run(
            "import typing\n"
            "T = typing.TypeVar('T', default=int)\n"
            "def id(x: T) -> T:\n    return x\n",
            {"functions": [NS + "id"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["templateParams"], [{
            "kind": "type",
            "name": "T",
            "default": {"nameParts": ["i64"]},
        }])

    def test_old_style_typevar_class_with_generic_base(self):
        # `class C(Generic[T])`: the `Generic[T]` base is unwrapped into
        # templateParams (not emitted as a regular base), matching the way
        # PEP 695 represents type parameters separately from the base list.
        mod = run(
            "from typing import TypeVar, Generic\n"
            "T = TypeVar('T', bound=int)\n"
            "class Box(Generic[T]):\n    value: T\n")
        ty = next(t for t in mod["types"] if t["qualifiedName"].endswith("Box"))
        self.assertEqual(ty["templateParams"], [{
            "kind": "type",
            "name": "T",
            "bound": {"nameParts": ["i64"]},
        }])
        # Generic[T] must not appear in baseClasses — the extractor strips it
        # before recording the base list. A Box with no other bases therefore
        # has no baseClasses key (omit-when-empty contract).
        self.assertNotIn("baseClasses", ty)

    def test_old_style_typevar_multiple_in_signature_ordered_by_first_use(self):
        # When multiple TypeVars appear, the order in templateParams is the
        # order of first use (return type first, then params left-to-right).
        # This is the most natural ordering for the host emitter.
        mod = run(
            "from typing import TypeVar\n"
            "K = TypeVar('K')\nV = TypeVar('V')\n"
            "def at(d, k: K) -> V:\n    return d[k]\n",
            {"functions": [NS + "at"]})
        fn = mod["functions"][0]
        # V first (return), K second (first param) — `d` has no annotation
        # so contributes nothing; only annotated positions count.
        self.assertEqual(fn["templateParams"], [
            {"kind": "type", "name": "V"},
            {"kind": "type", "name": "K"},
        ])

    def test_pep695_wins_over_module_typevar_with_same_name(self):
        # If a function uses PEP 695 brackets AND the module also has a
        # `T = TypeVar('T')`, the bracketed declaration is authoritative —
        # the module-level meta is not consulted to avoid duplicates.
        if sys.version_info < (3, 12):
            self.skipTest("PEP 695 syntax requires Python 3.12+")
        mod = run(
            "from typing import TypeVar\n"
            "T = TypeVar('T', bound=str)\n"
            "def id[T](x: T) -> T:\n    return x\n",
            {"functions": [NS + "id"]})
        fn = mod["functions"][0]
        # Only the PEP 695 bare T survives (no bound), not the module-level
        # `bound=str` declaration.
        self.assertEqual(fn["templateParams"], [{"kind": "type", "name": "T"}])

    # --- Old-style TypeVar constraint tuple ----------------------------

    def test_old_style_typevar_constraint_tuple_lifts_to_union_bound(self):
        # `T = TypeVar('T', int, str)` is a *constraint tuple*: T must be
        # exactly one of the listed types. That lowers to a union bound —
        # a TypeNode `union<int, str>` with the variant types carried
        # positionally in templateArgs (int → i64, str → string).
        mod = run(
            "from typing import TypeVar\n"
            "T = TypeVar('T', int, str)\n"
            "def pick(x: T) -> T:\n    return x\n",
            {"functions": [NS + "pick"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["templateParams"], [{
            "kind": "type",
            "name": "T",
            "bound": {
                "nameParts": ["union"],
                "templateArgs": [
                    {"nameParts": ["i64"]},
                    {"nameParts": ["string"]},
                ],
            },
        }])
        # A plain (non-generic) constraint tuple is fully modeled — no note,
        # no fidelity downgrade.
        self.assertEqual(fn["fidelity"], "source")
        self.assertEqual(fn["unsupported"], [])

    def test_constraint_tuple_three_members_preserves_order(self):
        # The union is order-preserving; a three-member tuple keeps all
        # three variants in source order.
        mod = run(
            "from typing import TypeVar\n"
            "N = TypeVar('N', int, float, bool)\n"
            "def norm(x: N) -> N:\n    return x\n",
            {"functions": [NS + "norm"]})
        fn = mod["functions"][0]
        bound = fn["templateParams"][0]["bound"]
        self.assertEqual(bound["nameParts"], ["union"])
        self.assertEqual(
            [a["nameParts"] for a in bound["templateArgs"]],
            [["i64"], ["f64"], ["bool"]])

    def test_constraint_tuple_with_generic_member_drops_and_degrades(self):
        # Out of scope: a constraint tuple whose members include a
        # parameterised generic (`list[int]`) is dropped wholesale — no
        # half-formed union reaches the wire. The TypeVar still surfaces as
        # a bare `T`, and the consuming function carries an unsupported note
        # plus an `inferred` fidelity downgrade.
        mod = run(
            "from typing import TypeVar\n"
            "T = TypeVar('T', list[int], dict)\n"
            "def pick(x: T) -> T:\n    return x\n",
            {"functions": [NS + "pick"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["templateParams"], [{"kind": "type", "name": "T"}])
        self.assertEqual(fn["fidelity"], "inferred")
        self.assertTrue(any("constraint tuple" in n for n in fn["unsupported"]),
                        f"expected a constraint-tuple note; got {fn['unsupported']}")

    def test_non_generic_function_has_no_templateparams_key(self):
        mod = run("def plain(x: int) -> int:\n    return x\n",
                  {"functions": [NS + "plain"]})
        self.assertNotIn("templateParams", mod["functions"][0])

    # --- Classes / inheritance -------------------------------------------

    def test_class_with_base_extracts_baseclasses_and_kinds(self):
        mod = run(
            "class Animal:\n    name: str\n"
            "class Dog(Animal):\n    breed: str\n")
        # types[0] = Animal (no base), types[1] = Dog (Animal base, kind=class)
        animal = next(t for t in mod["types"] if t["qualifiedName"] == NS + "Animal")
        dog = next(t for t in mod["types"] if t["qualifiedName"] == NS + "Dog")
        self.assertNotIn("baseClasses", animal)
        self.assertEqual([b["nameParts"][0] for b in dog["baseClasses"]],
                         ["Animal"])
        # Python has no class/interface distinction; everything is "class".
        # The Java/V8 emitters' interface-only-class invariants don't apply
        # to Python-sourced types (every base is Class kind).
        self.assertEqual(dog["baseClassKinds"], ["class"])

    def test_class_with_two_bases(self):
        mod = run(
            "class A: pass\n"
            "class B: pass\n"
            "class C(A, B):\n    x: int\n")
        c = next(t for t in mod["types"] if t["qualifiedName"] == NS + "C")
        self.assertEqual([b["nameParts"][0] for b in c["baseClasses"]],
                         ["A", "B"])
        self.assertEqual(c["baseClassKinds"], ["class", "class"])

    def test_class_fields_typed_via_annassign(self):
        mod = run("class Point:\n    x: float\n    y: float\n")
        pt = mod["types"][0]
        self.assertEqual(pt["qualifiedName"], NS + "Point")
        names = [f["name"] for f in pt["fields"]]
        self.assertEqual(names, ["x", "y"])
        for f in pt["fields"]:
            self.assertEqual(f["type"]["nameParts"], ["f64"])

    def test_plain_class_without_bases_omits_keys(self):
        mod = run("class Plain:\n    x: int\n")
        t = mod["types"][0]
        self.assertNotIn("baseClasses", t)
        self.assertNotIn("baseClassKinds", t)

    def test_pep695_class_generic(self):
        if sys.version_info < (3, 12):
            self.skipTest("PEP 695 syntax requires Python 3.12+")
        mod = run("class Box[T]:\n    value: T\n")
        ty = mod["types"][0]
        self.assertEqual(ty["templateParams"], [{"kind": "type", "name": "T"}])
        self.assertEqual(ty["fidelity"], "source")

    def test_pep695_class_single_bound_captures_bound(self):
        if sys.version_info < (3, 12):
            self.skipTest("PEP 695 syntax requires Python 3.12+")
        # Class-side mirrors function-side: `[T: int]` is captured into the
        # wire `bound` (`int → i64`); no fidelity downgrade is needed since
        # nothing was dropped.
        mod = run("class Sortable[T: int]:\n    items: list[T]\n")
        ty = mod["types"][0]
        self.assertEqual(ty["templateParams"], [{
            "kind": "type",
            "name": "T",
            "bound": {"nameParts": ["i64"]},
        }])
        self.assertEqual(ty["fidelity"], "source")

    def test_pep696_class_default_captures_default(self):
        if sys.version_info < (3, 13):
            self.skipTest("PEP 696 type-parameter defaults require Python 3.13+")
        # Class-side mirrors function-side: `[T = int]` is captured into the
        # wire `default` (`int → i64`). PEP 696 allows defaults on both
        # classes and functions.
        mod = run("class Box[T = int]:\n    value: T\n")
        ty = mod["types"][0]
        self.assertEqual(ty["templateParams"], [{
            "kind": "type",
            "name": "T",
            "default": {"nameParts": ["i64"]},
        }])
        self.assertEqual(ty["fidelity"], "source")

    # --- Types are always collected (independent of functions filter) ----

    def test_types_extracted_independently_of_functions_filter(self):
        mod = run(
            "class A: pass\ndef fn() -> None: return None\n",
            {"functions": [NS + "fn"]})
        self.assertEqual(len(mod["functions"]), 1)
        self.assertEqual(mod["types"][0]["qualifiedName"], NS + "A")

    # --- Type annotation mapping -----------------------------------------

    def test_list_annotation_maps_to_slice(self):
        mod = run("def take(xs: list[int]) -> None: return None\n",
                  {"functions": [NS + "take"]})
        t = mod["functions"][0]["params"][0]["type"]
        self.assertEqual(t["nameParts"], ["slice"])
        self.assertEqual(t["templateArgs"][0]["nameParts"], ["i64"])

    def test_optional_annotation_maps_to_optional(self):
        mod = run("def maybe(x: Optional[str]) -> None: return None\n",
                  {"functions": [NS + "maybe"]})
        t = mod["functions"][0]["params"][0]["type"]
        self.assertEqual(t["nameParts"], ["optional"])
        self.assertEqual(t["templateArgs"][0]["nameParts"], ["string"])

    def test_none_return_maps_to_void(self):
        mod = run("def proc(n: int) -> None:\n    return None\n",
                  {"functions": [NS + "proc"]})
        self.assertEqual(mod["functions"][0]["returnType"]["nameParts"], ["void"])

    # --- Unknown / unsupported annotation safety -------------------------

    def test_pep604_union_lifts_to_union_type(self):
        # PEP 604 `int | str` now lifts to a proper union TypeNode rather
        # than falling through to the stringify fallback for unknown
        # annotations.
        mod = run("def pick(x: int | str) -> int | str:\n    return x\n",
                  {"functions": [NS + "pick"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["returnType"]["nameParts"], ["union"])
        member_kinds = [a["nameParts"] for a in fn["returnType"]["templateArgs"]]
        self.assertEqual(member_kinds, [["i64"], ["string"]])
        # Union is fully modelled — no fidelity downgrade.
        self.assertEqual(fn["fidelity"], "source")
        self.assertEqual(fn["unsupported"], [])

    def test_pep604_three_member_union_preserves_order(self):
        mod = run(
            "def norm(x: int | float | bool) -> bool:\n    return True\n",
            {"functions": [NS + "norm"]})
        fn = mod["functions"][0]
        members = fn["params"][0]["type"]
        self.assertEqual(members["nameParts"], ["union"])
        self.assertEqual([a["nameParts"] for a in members["templateArgs"]],
                         [["i64"], ["f64"], ["bool"]])

    def test_typing_union_subscript_also_lifts(self):
        mod = run(
            "from typing import Union\n"
            "def pick(x: Union[int, str]) -> Union[int, str]:\n    return x\n",
            {"functions": [NS + "pick"]})
        fn = mod["functions"][0]
        self.assertEqual(fn["returnType"]["nameParts"], ["union"])
        self.assertEqual(
            [a["nameParts"] for a in fn["returnType"]["templateArgs"]],
            [["i64"], ["string"]])

    def test_unknown_annotation_marker_is_reserved_token(self):
        # An ast shape outside the recognised set (PEP 646 unpacked
        # TypeVarTuple via ast.Starred) used to leak raw source like
        # ``tuple[*Ts]`` into nameParts[0]. The replacement marker is a
        # reserved token so a host emitter rendering it produces a
        # clearly-broken identifier instead of silently broken code.
        mod = run(
            "def take(xs: tuple[*tuple[int, str]]) -> None:\n"
            "    return None\n",
            {"functions": [NS + "take"]})
        fn = mod["functions"][0]
        # The fidelity downgrade carries the original source for triage.
        self.assertEqual(fn["fidelity"], "inferred")
        self.assertTrue(
            any("unsupported type annotation" in u
                for u in fn["unsupported"]),
            f"missing unsupported note: {fn['unsupported']}")
        # Walk the param's templateArgs to find the marker; the marker
        # is the reserved ``__topo_unsupported_type__`` token, not raw
        # source text containing ``*``.
        params = fn["params"]
        self.assertTrue(params)
        outer = params[0]["type"]
        self.assertIn("templateArgs", outer)
        # tuple[ *X ] — the X inner that the extractor cannot model
        # becomes the reserved marker, not raw source. We walk every
        # nested nameParts and assert no leaked source-text characters
        # ('*', '|', etc.) appear in any nameParts entry.
        def walk(node):
            for part in node.get("nameParts", []):
                self.assertNotIn("*", part,
                                 f"raw '*' leaked into nameParts: {node}")
                self.assertNotIn("|", part,
                                 f"raw '|' leaked into nameParts: {node}")
            for child in node.get("templateArgs", []):
                walk(child)
        walk(outer)

    # --- Implicit module namespace from basename ------------------------

    def test_implicit_module_namespace_pins_qname(self):
        # `calc.py`'s top-level `def fib` keys as `calc::fib`, mirroring
        # how `import calc; calc.fib(...)` works in Python. This is the
        # rule the TranspileFromPython equivalence test relies on so a
        # `.topo` declaration of `namespace calc { f64 fib(f64 n); }` is
        # matched without an extra namespace wrapper in the source.
        mod = run("def fib(n: int) -> int:\n    return n\n",
                  {"functions": ["calc::fib"]},
                  module_name="calc")
        self.assertEqual(mod["functions"][0]["qualifiedName"], "calc::fib")


def run_bytes(raw: bytes, request_overrides: dict | None = None,
              module_name: str = "_smoke") -> "subprocess.CompletedProcess":
    """Run the extractor over a file written from *raw bytes*.

    Unlike ``run`` (which writes UTF-8 text) this lets a test stage a
    BOM-prefixed or non-UTF-8 source file to exercise the decode path.
    Returns the raw CompletedProcess so callers can assert on both the
    exit code (batch must not abort) and stderr (graceful-degrade note).
    """
    tmpdir = tempfile.mkdtemp(prefix="topo-extract-py-bytes-")
    path = os.path.join(tmpdir, module_name + ".py")
    try:
        with open(path, "wb") as f:
            f.write(raw)
        req = {"files": [path], "functions": [], "symbolTable": {}}
        if request_overrides:
            req.update(request_overrides)
        return subprocess.run(
            [sys.executable, SCRIPT],
            input=json.dumps(req),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    finally:
        try:
            os.unlink(path)
        finally:
            os.rmdir(tmpdir)


class EncodingDegradation(unittest.TestCase):
    """A non-UTF-8 / BOM-prefixed source file must degrade gracefully
    rather than crash the whole transpile batch (regression for the
    host-extractor encoding issue, instance #12). The fix reads with
    ``utf-8-sig`` (transparent BOM strip) and falls back to
    ``errors="replace"`` on a UnicodeDecodeError.
    """

    def test_utf8_bom_prefixed_file_lifts_symbol(self):
        # A leading UTF-8 BOM must not corrupt the parse: utf-8-sig strips
        # it, so the declared symbol still lifts and the batch exits 0.
        src = "def add3(x: int) -> int:\n    return x + 3\n"
        raw = b"\xef\xbb\xbf" + src.encode("utf-8")
        res = run_bytes(raw, {"functions": [NS + "add3"]})
        self.assertEqual(res.returncode, 0, res.stderr)
        mod = json.loads(res.stdout)
        self.assertEqual(mod["functions"][0]["qualifiedName"], NS + "add3")

    def test_non_utf8_byte_does_not_abort_batch(self):
        # A stray non-UTF-8 byte in a comment used to raise
        # UnicodeDecodeError and abort the entire batch (return 1, no
        # output). It must now degrade: the file is re-read with
        # errors="replace", the surrounding declared symbol still lifts,
        # and a diagnostic is written to stderr.
        src = ("# caf\xe9 latte\n"  # 0xE9 alone is invalid UTF-8
               "def add3(x: int) -> int:\n    return x + 3\n")
        raw = src.encode("latin-1")
        res = run_bytes(raw, {"functions": [NS + "add3"]})
        self.assertEqual(res.returncode, 0, res.stderr)
        mod = json.loads(res.stdout)
        self.assertEqual(mod["functions"][0]["qualifiedName"], NS + "add3")
        # The substitution is reported (not silent) so coverage loss is
        # visible to the operator.
        self.assertIn("non-UTF-8", res.stderr)


if __name__ == "__main__":
    unittest.main()
