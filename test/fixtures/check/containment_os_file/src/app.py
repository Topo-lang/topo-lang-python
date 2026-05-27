import os

def read_config(key):
    f = open("config.txt", "r")
    data = f.read()
    f.close()
    return key
