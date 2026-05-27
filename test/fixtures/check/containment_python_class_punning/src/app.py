class Original:
    label = "safe"


class Evil:
    label = "compromised"


def mutate(code):
    obj = Original()
    obj.__class__ = Evil
    return code
