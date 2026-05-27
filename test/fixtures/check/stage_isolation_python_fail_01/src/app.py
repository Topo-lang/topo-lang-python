# `init` (stage<1>) directly calls `process` (stage<2>). Forward stage
# call — violation.


def process():
    pass  # stage 2 work


def init():
    process()  # forward stage call — violation


def run():
    init()
    process()
