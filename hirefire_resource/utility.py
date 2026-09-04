import math


def round_half_up(value: float, ndigits: int = 0) -> float:
    if ndigits <= 0:
        return float(math.floor(value + 0.5))
    factor = 10**ndigits
    return math.floor(value * factor + 0.5) / factor
