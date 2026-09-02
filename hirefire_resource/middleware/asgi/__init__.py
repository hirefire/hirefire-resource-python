from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from hirefire_resource.middleware import present_header

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def request_start_from_scope(scope: Mapping[str, Any]) -> str | None:
    request_start = None
    queue_start = None
    for header_name, header_value in scope.get("headers", []):
        if isinstance(header_name, (bytes, bytearray)):
            name = header_name.lower()
        else:
            name = str(header_name).lower().encode("utf-8")
        if isinstance(header_value, (bytes, bytearray)):
            raw = header_value.decode("utf-8", "replace")
        else:
            raw = str(header_value)
        if name == b"x-request-start" and request_start is None:
            request_start = raw
        elif name == b"x-queue-start" and queue_start is None:
            queue_start = raw
    return present_header(request_start) or present_header(queue_start)
