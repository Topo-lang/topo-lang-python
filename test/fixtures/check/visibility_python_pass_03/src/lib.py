# Private helpers called only from within the same namespace.


def normalize():
    pass


def helper():
    normalize()  # private to private, same namespace — OK


def compute():
    helper()      # public to private, same namespace — OK
    normalize()   # public to private, same namespace — OK


def finalize():
    helper()      # public to private, same namespace — OK


def run():
    compute()
    finalize()
