# Parallel stage function `compute` writes to a module-level global via
# explicit `global` declaration. Expected: purity violation for `compute`.

counter = 0


def compute():
    global counter
    counter = counter + 1


def render():
    # pure — does not touch globals
    pass
