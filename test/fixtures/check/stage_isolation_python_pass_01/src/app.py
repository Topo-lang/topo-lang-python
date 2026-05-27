# `init` and `process` never call each other — the host call graph
# respects the stage ordering declared in .topo.


def init():
    local = 0
    local = local + 1
    _ = local


def process():
    tmp = 42
    _ = tmp


def run():
    init()
    process()
