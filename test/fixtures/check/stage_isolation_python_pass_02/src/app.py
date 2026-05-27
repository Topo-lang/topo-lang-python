# Both `taskA` and `taskB` are in stage<1>. Same-stage calls are allowed.


def taskB():
    pass


def taskA():
    taskB()  # same-stage call — OK


def run():
    taskA()
    taskB()
