# Three parallel-stage functions each write a different module global.
# Expected: at least 3 purity errors.

buffer = 0
processed = 0
total_bytes = 0


def producer():
    global buffer
    buffer = 1  # write global #1


def consumer():
    global processed
    processed = processed + 1  # write global #2


def sideEffect():
    global total_bytes
    total_bytes += 100  # write global #3 (compound)
