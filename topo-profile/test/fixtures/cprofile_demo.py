#!/usr/bin/env python3
# Fixture for the cProfile NDJSON harness e2e test.
#
# Small script with several functions and nested calls so the harness
# captures a non-trivial call edge set. cProfile is deterministic; this
# script does not need to do real work, just exercise call diversity.
#
# Functions:
#     compute(n)  — top-level driver; calls helper() in a loop
#     helper(x)   — middle layer; calls nested(x) and twiddle(x)
#     nested(x)   — leaf computation
#     twiddle(x)  — leaf computation
#
# Expected call edges captured by cProfile:
#     <module> -> compute
#     compute  -> helper        (10 times)
#     helper   -> nested        (10 times)
#     helper   -> twiddle       (10 times)


def twiddle(x: int) -> int:
    return (x * 31 + 7) & 0xFFFF


def nested(x: int) -> int:
    acc = 0
    for i in range(x):
        acc = (acc + i) & 0xFFFF
    return acc


def helper(x: int) -> int:
    a = nested(x)
    b = twiddle(x)
    return a ^ b


def compute(n: int) -> int:
    total = 0
    for i in range(n):
        total = (total + helper(i + 1)) & 0xFFFF
    return total


if __name__ == "__main__":
    result = compute(10)
    # Suppress in default harness mode; this print only shows up if the
    # caller passes --target-stdout.
    print(f"result={result}")
