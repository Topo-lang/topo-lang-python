# `lib.alpha` and `lib.beta` are private. `consumer.drive` reaches both
# of them across the namespace boundary — two distinct violations.


def alpha():
    pass


def beta():
    pass


def api():
    alpha()  # same-namespace — OK
    beta()   # same-namespace — OK


def run():
    api()
