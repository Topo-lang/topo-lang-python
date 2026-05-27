# All parallel stage-1 functions are pure: they only use locals.
# No module-level globals are declared, so no writes can be attributed.


def helper_double(x):
    result = x * 2
    return result


def transform():
    tmp = 5
    tmp = helper_double(tmp)
    _ = tmp


def validate():
    a = 1
    b = 2
    s = a + b
    _ = s
