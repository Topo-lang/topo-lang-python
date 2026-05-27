# `consumer.drive` reaches into `lib.alpha` AND `lib.beta`, both of
# which are declared private in namespace `lib`. Two distinct violations.


def drive():
    import lib
    lib.alpha()  # cross-namespace private call — violation #1
    lib.beta()   # cross-namespace private call — violation #2
    lib.api()    # public — OK
