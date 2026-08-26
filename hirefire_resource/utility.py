import math

from hirefire_resource.errors import MissingQueueError


def round_half_up(value: float, ndigits: int = 0) -> float:
    if ndigits <= 0:
        return float(math.floor(value + 0.5))
    factor = 10**ndigits
    return math.floor(value * factor + 0.5) / factor


def normalize_queues(*queues: object, allow_empty: bool) -> set[str]:
    names: set[str] = set()
    for queue in queues:
        name = "" if queue is None else str(queue).strip()
        if name:
            names.add(name)

    if names:
        return names
    if allow_empty:
        return set()
    raise MissingQueueError()
