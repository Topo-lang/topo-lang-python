# All declared functions are public — any cross-function call is legal.


def stepA():
    pass


def stepB():
    stepA()  # public to public — OK


def run():
    stepA()
    stepB()
