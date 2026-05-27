import os


def outer():
    def inner():
        os.system("inner")
    os.system("outer")
    inner()
