from hirefire_resource.errors import MissingQueueError


def normalize_queues(*queues: object, allow_empty: bool) -> set[str]:
    names: set[str] = set()
    flat: list[object] = []
    for queue in queues:
        if isinstance(queue, (list, tuple)):
            flat.extend(queue)
        else:
            flat.append(queue)
    for queue in flat:
        name = "" if queue is None else str(queue).strip()
        if name:
            names.add(name)

    if names:
        return names
    if allow_empty:
        return set()
    raise MissingQueueError()
