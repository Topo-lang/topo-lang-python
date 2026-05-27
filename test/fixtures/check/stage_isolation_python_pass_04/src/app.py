# `init` (stage<1>) directly calls `process` (stage<2>) — would be a
# forward-stage violation under [stage_isolation].mode = "force", but
# mode = "off" emits a Note and exits 0.


def init():
    process()  # forward stage call — would violate, but mode=off


def process():
    pass


def run():
    init()
    process()
