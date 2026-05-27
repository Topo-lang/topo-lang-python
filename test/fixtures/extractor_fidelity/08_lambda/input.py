import os


def register():
    callback = lambda: os.system("echo hi")
    return callback
