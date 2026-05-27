# Project-multi Python fixture — host script (counterpart of
# cpp/rust/java project_multi). Two `list[int]` locals in
# the same frame; summary template references both via `{sum(a)}` /
# `{sum(b)}`, so a single adapter spawn resolves all placeholders.
#
# Expected at the breakpoint (`sentinel = 0`):
#   sum(a)              = 1+2+3+4         = 10
#   sum(b)              = 10+20+30+40     = 100
#   sum(a) + sum(b)                       = 110
#   max(b) - max(a)     = 40 - 4          = 36
#
# Trailing summation keeps both lists alive past the sentinel so the bytecode
# optimizer can't elide them.
import sys


def main():
    a = [1, 2, 3, 4]
    b = [10, 20, 30, 40]
    sentinel = 0  # breakpoint here — adapter reads `a` and `b`
    total = sentinel
    for i in range(len(a)):
        total += a[i] + b[i]
    print("done", total, file=sys.stderr)
    return (a, b)


if __name__ == "__main__":
    main()
