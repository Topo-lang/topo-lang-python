import sys


def _tracer(frame, event, arg):
    return _tracer


def install(code):
    sys.settrace(_tracer)
    return code
