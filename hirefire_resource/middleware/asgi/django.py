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

    Reads ``X-Request-Start`` (or ``X-Queue-Start``) from the ASGI scope.
    Websocket and lifespan scopes pass through without sampling. Wrap the ASGI
    application in ``asgi.py`` (for example
    ``application = HireFireMiddleware(get_asgi_application())``). Do not put it
    in ``MIDDLEWARE``: that stack is for Django middleware classes, not raw ASGI
    wrappers.

    When a token is present, records a queue-time sample under the process HTTP
    name and starts the dispatcher. Explicit http registration is optional.
    Failures are logged and swallowed so the host app is unaffected.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            process_request_queue_time(extract=lambda: request_start_from_scope(scope))
        await self.inner(scope, receive, send)
