# `process` (stage<2>) calls `init` (stage<1>). Backward stage calls
# are allowed — the later stage reads earlier-stage outputs.


def init():
    pass


def process():
    init()  # backward call (stage 2 -> stage 1) — OK


def run():
    init()
    process()
