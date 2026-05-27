# `attacker` is NOT declared in .topo — calling an `internal` function
# from it is a visibility violation. The .topo declares `initInternal`
# as `internal:`, so only declared callers may reference it.


def initInternal():
    pass


def bootstrap():
    initInternal()  # declared caller — OK


def run():
    bootstrap()


def attacker():
    initInternal()  # violation: internal called from external (undeclared)
