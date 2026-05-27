# `secret_op` is private to namespace `storage`. Calls from within
# `storage.py` are allowed; cross-module calls are not.


def secret_op():
    pass


def put():
    secret_op()  # same-namespace private call — OK


def run():
    put()
