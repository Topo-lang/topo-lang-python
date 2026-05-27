# Project-simple Python fixture — host script (counterpart of
# cpp/rust/java project_simple, driven by the debugpy DAP adapter).
#
# Companion to main.topo. The breakpoint fires on the `sentinel = 0` line
# after `data` is fully populated. debugpy evaluates `data` against the
# frame's locals; the adapter packs it into raw little-endian bytes.
#
# Data values mirror Java's project_simple so half/full sums are visibly
# distinct:
#   first_half  (data[0..4]) = 1+2+3+4       = 10
#   second_half (data[4..8]) = 10+20+30+40   = 100
#   total       (sum(data))                  = 110
#   shape(data)                              = [8]
#   dtype(data)                              = i64  (Python int → 8-byte signed)
#
# Lives inside `main()` so the frame is a real function (the topo-debug-python
# adapter evaluates against the breakpoint's frame; module top-level scripts
# are awkward to stop in deterministically). Trailing reference keeps the
# list alive past the breakpoint so the bytecode optimizer can't elide it.
import sys


def main():
    data = [1, 2, 3, 4, 10, 20, 30, 40]
    sentinel = 0  # breakpoint here — adapter reads `data` at this line
    total = sentinel
    for v in data:
        total += v
    print("done", total, file=sys.stderr)
    return data


if __name__ == "__main__":
    main()
