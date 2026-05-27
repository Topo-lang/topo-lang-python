# Sequential stages: `init` (stage<1>) and `finalize` (stage<2>) are NOT
# in a parallel stage, so writes to module-level globals are allowed.

g_state = 0
g_count = 0


def init():
    global g_state, g_count
    g_state = 1
    g_count = g_count + 1


def finalize():
    global g_state, g_count
    g_state = 2
    g_count = g_count + 10
