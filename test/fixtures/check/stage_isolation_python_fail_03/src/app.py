# Both `loadA` and `loadB` (stage<1>) call `merge` (stage<2>) — two
# forward stage violations in the same fn block.


def merge():
    pass  # stage 2


def loadA():
    merge()  # violation #1: stage 1 -> stage 2


def loadB():
    merge()  # violation #2: stage 1 -> stage 2


def run():
    loadA()
    loadB()
    merge()
