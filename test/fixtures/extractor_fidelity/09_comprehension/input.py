import os


def list_files():
    names = [os.path.basename(p) for p in os.listdir(".")]
    return names
