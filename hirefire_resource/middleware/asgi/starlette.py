from typing import Any

from hirefire_resource.middleware import process_request_queue_time
from hirefire_resource.middleware.asgi import (
    Receive,
    Scope,
    Send,
    request_start_from_scope,
)


class HireFireMiddleware:
    """Starlette/FastAPI middleware that samples request queue time.

    Install by wrapping the app (``app = HireFireMiddleware(app)``) or, with FastAPI,
    ``app.add_middleware(HireFireMiddleware)``. Records a queue-time sample from
    ``X-Request-Start`` (or ``X-Queue-Start``) on each HTTP request when a token is
    present.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            process_request_queue_time(extract=lambda: request_start_from_scope(scope))
        await self.app(scope, receive, send)
