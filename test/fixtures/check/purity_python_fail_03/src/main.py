# `tick` and `monitor` both write to the global `ticks` via compound
# assignment. Two distinct violations expected.

ticks = 0


def tick():
    global ticks
    ticks += 1  # compound assignment — write


def monitor():
    global ticks
    ticks -= 1  # compound assignment — write
