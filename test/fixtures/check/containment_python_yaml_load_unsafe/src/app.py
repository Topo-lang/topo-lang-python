import yaml


def load_config(code):
    text = "key: 1"
    data = yaml.load(text)
    return code
