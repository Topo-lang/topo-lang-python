class Box:
    value = 42


def fetch_value(code):
    box = Box()
    # Static literal attribute name. Conservative posture: still flagged
    # because L1 cannot inspect arguments.
    return getattr(box, "value")
