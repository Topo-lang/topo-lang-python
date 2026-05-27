# Live target for the sys.monitoring harness self-test (only
# registered when configure-time finds Python >= 3.12). Mirrors the
# cProfile demo: a couple of nested calls whose names look like declared
# stage boundaries so the produced PY_START/PY_RETURN stream joins to
# declarations by function identity.


def stage_load(n):
    return [i * i for i in range(n)]


def stage_transform(xs):
    return sum(x + 1 for x in xs)


def compute(n):
    return stage_transform(stage_load(n))


def main():
    total = 0
    for _ in range(50):
        total += compute(16)
    return total


if __name__ == "__main__":
    main()
