# `acquire` (stage<1>) and `transform` (stage<2>) both call `store`
# (stage<3>) directly — two forward-stage violations.


def store():
    pass  # stage 3


def transform():
    store()  # violation #1: stage 2 -> stage 3


def acquire():
    store()  # violation #2: stage 1 -> stage 3


def run():
    acquire()
    transform()
    store()
