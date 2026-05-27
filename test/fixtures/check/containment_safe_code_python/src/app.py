import math
import itertools
from collections import defaultdict

def compute(n):
    values = list(range(1, n + 1))
    total = sum(v * v for v in values)
    pairs = list(itertools.combinations(values, 2))
    freq = defaultdict(int)
    for a, b in pairs:
        freq[a + b] += 1
    return int(math.sqrt(total))

def average(count):
    if count <= 0:
        return 0.0
    values = [math.sin(i) for i in range(count)]
    return sum(values) / len(values)
