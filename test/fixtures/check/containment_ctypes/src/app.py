import ctypes

def call_native(x):
    lib = ctypes.CDLL("libm.so")
    return x
