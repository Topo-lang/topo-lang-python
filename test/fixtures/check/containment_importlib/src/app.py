import importlib

def load_plugin(id):
    mod = importlib.import_module("plugins.mod_" + str(id))
    return 0
