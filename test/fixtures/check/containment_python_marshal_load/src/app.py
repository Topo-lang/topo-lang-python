import marshal


def parse_blob(code):
    raw = b"\x00\x00\x00\x00"
    obj = marshal.loads(raw)
    return code
