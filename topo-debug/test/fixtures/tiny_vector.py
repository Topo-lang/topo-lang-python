# Fixture for topo-debug-python.
#
# A plain Python list of floats lives in main(). The breakpoint targets the
# `sentinel = 0` line; by then `vec` has been bound, mirroring tiny_vector.cpp's
# f64-ramp fixture used by topo-debug-cpp's e2e tests.
#
# The breakpoint line is the line of `sentinel = 0` below — currently line 14.
# If you re-arrange this file, update the `--break tiny_vector.py:<N>` value
# in topo-core/test/CMakeLists.txt.
import sys


def main():
    vec = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5]
    sentinel = 0  # breakpoint here — adapter reads `vec` at this line
    print("done", sentinel, file=sys.stderr)
    return vec


if __name__ == "__main__":
    main()
