# `helper` is declared private in namespace `app`. Same-namespace calls
# from `compute` and `run` are allowed.


def helper():
    pass


def compute():
    helper()  # same-namespace private call — OK


def run():
    compute()
