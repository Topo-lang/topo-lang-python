# `probe.leak` reaches into `storage.secret_op`, which is declared
# private in namespace `storage`. Expected: one violation.


def leak():
    import storage
    storage.secret_op()  # cross-namespace private call — violation
