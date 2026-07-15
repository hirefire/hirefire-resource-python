from hirefire_resource.errors import MissingQueueError


def normalize_queues(*queues: str, allow_empty: bool = False) -> set[str]:
    names: set[str] = set()
    for queue in queues:
        name = queue.strip()
        if name:
            names.add(name)

    if names:
        return names
    if allow_empty:
        return set()
    raise MissingQueueError()
