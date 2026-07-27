from typing import Any

from hirefire_resource.middleware import process_request_queue_time
from hirefire_resource.middleware.asgi import (
    Receive,
    Scope,
    Send,
    request_start_from_scope,
)


class HireFireMiddleware:
    """Django ASGI middleware that samples request queue time.

    Reads ``X-Request-Start`` (or ``X-Queue-Start``) from the ASGI scope and records a
    queue-time sample on HTTP requests when a token is present (websocket and lifespan
    scopes pass through without sampling). Explicit http registration is optional.
    Wrap the ASGI application in ``asgi.py`` (for example
    ``application = HireFireMiddleware(get_asgi_application())``). Do not put it in
    ``MIDDLEWARE``: that stack is for Django middleware classes, not raw ASGI wrappers.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            process_request_queue_time(extract=lambda: request_start_from_scope(scope))
        await self.inner(scope, receive, send)
