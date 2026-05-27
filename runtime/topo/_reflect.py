"""Map idiomatic Python type annotations onto topo stdlib type spellings.

The C++ projection derives In/Out from the function type via
``function_traits``; the Python projection of the same philosophy reads
``__annotations__``. The contract is identical: In/Out live in the
signature, not in hand-written ``.topo``.

Scalar mapping follows the Python stdlib aliases used by ``topo-init``
(``std::python::{int,float,bool,str}``). A multi-field structure is a
PEP 604 / typing ``tuple[...]`` whose elements are themselves annotated
``(name, type)`` pairs — that is how the Python host maps ``record<...>``
to an ordered tuple[...], explicit and stable, never inferred.
"""

from __future__ import annotations

import inspect
import typing
from typing import Optional

from ._graph import TypeRef

_SCALAR = {
    int: "int",
    float: "float",
    bool: "bool",
    str: "str",
}


def _is_record(annotation) -> bool:
    origin = typing.get_origin(annotation)
    return origin is tuple


def _record_fields(annotation):
    """A record annotation is ``tuple[Field, ...]`` where each ``Field``
    is ``typing.Annotated[<scalar/record>, "<field name>"]``. Field names
    are carried in the Annotated metadata so the order and naming are
    explicit and stable, never inferred."""

    fields = []
    for elem in typing.get_args(annotation):
        if typing.get_origin(elem) is not None and hasattr(elem, "__metadata__"):
            base = elem.__args__[0]
            meta = elem.__metadata__
        else:
            raise TypeError(
                "record fields must be Annotated[<type>, '<field name>']; "
                f"got {elem!r}"
            )
        if not meta or not isinstance(meta[0], str):
            raise TypeError("record field name (str) missing in Annotated metadata")
        fields.append((meta[0], _to_typeref(base)))
    if not fields:
        # Mirrors core Sema: record<> with no field is rejected upstream;
        # fail early here so the user sees it before emission.
        raise TypeError("record type must declare at least one field")
    return tuple(fields)


def _to_typeref(annotation) -> TypeRef:
    if annotation in _SCALAR:
        return TypeRef(scalar=_SCALAR[annotation])
    if _is_record(annotation):
        return TypeRef(record=_record_fields(annotation))
    raise TypeError(
        f"unsupported handler type {annotation!r}; use int/float/bool/str "
        "or tuple[Annotated[T, 'field'], ...] for a record"
    )


def reflect_signature(fn) -> tuple[Optional[TypeRef], TypeRef]:
    """Return (In, Out). In is None for a source handler (no parameter).

    A handler is a pure Functor: at most one input. More than one
    parameter is rejected here with the same intent as the core Parser's
    "a handler takes at most one input" rule, so the user never reaches
    emission with an unrepresentable signature.
    """

    hints = typing.get_type_hints(fn, include_extras=True)
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters if p != "return"]
    if len(params) > 1:
        raise TypeError(
            f"handler '{fn.__name__}' has {len(params)} inputs; a handler is "
            "a pure Functor with at most one input — aggregate into a record"
        )
    in_type = None
    if params:
        pname = params[0]
        if pname not in hints:
            raise TypeError(f"handler '{fn.__name__}' input '{pname}' is unannotated")
        in_type = _to_typeref(hints[pname])
    if "return" not in hints:
        raise TypeError(f"handler '{fn.__name__}' has no return annotation")
    out_type = _to_typeref(hints["return"])
    return in_type, out_type
