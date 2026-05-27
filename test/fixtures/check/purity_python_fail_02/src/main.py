# `stepA` writes to a module-level list `shared_state` via subscript and
# attribute writes — both detected by the L1 conservative analysis.

shared_state = [0, 0, 0]


def stepA():
    shared_state[0] = 42  # subscript write to a module global — violation


def stepB():
    local = 1
    _ = local
