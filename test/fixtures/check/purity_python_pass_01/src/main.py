# Pure functions: no module-level globals are touched.
# Parallel stage-1 functions operate only on locals and parameters.


def compute_helper(a, b):
    result = a + b
    return result


def compute():
    local = 42
    local = local + 1  # local assignment is fine
    _ = compute_helper(local, 10)


def render():
    x = 5
    y = 10
    _ = x + y
