# Python host source matching minimal.topo.
#
# Demonstrates the Python idioms that PythonEmitter produces for the 6
# first-batch stdlib bridging types (see PythonEmitter):
#
#   bool        -> bool
#   i64         -> int
#   f64         -> float
#   string      -> str
#   optional<T> -> "T | None"   (PEP 604, Python >= 3.10)
#   slice<T>    -> "list[T]"
#
# `slice<T>` ABI semantics ({u32 len, T* ptr}) intentionally degrade to
# `list[T]` in Python value semantics — the boundary copy is acceptable for
# first-batch use cases. See PythonEmitter for the TODO on memoryview /
# numpy.ndarray for byte / numeric T in Phase 2.


def isReady() -> bool:
    return True


def nextId() -> int:
    return 42


def averageScore() -> float:
    return 0.75


def label() -> str:
    return "topo"


def maybeFlag() -> bool | None:
    return None


def samples() -> list[float]:
    return [1.0, 2.0, 3.0]


def boundary(id: int,
             name: str,
             flags: bool | None,
             values: list[float]) -> int | None:
    if flags is None:
        return None
    return id if name and values else None
