from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def request_start_from_scope(scope: Mapping[str, Any]) -> str | None:
    queue_start = None
    for header_name, header_value in scope.get("headers", []):
        name = header_name.lower()
        if name == b"x-request-start":
            return header_value.decode("utf-8", "replace")
        if name == b"x-queue-start":
            queue_start = header_value.decode("utf-8", "replace")
    return queue_start
