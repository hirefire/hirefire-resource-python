from collections.abc import Iterable, Mapping
from typing import Any

from hirefire_resource.middleware import process_request_queue_time
from hirefire_resource.middleware.wsgi import request_start_from_environ


class HireFireMiddleware:
    """Flask middleware that samples request queue time.

    Install with ``app.wsgi_app = HireFireMiddleware(app)``. Records a queue-time
    sample from ``X-Request-Start`` (or ``X-Queue-Start``) on each request when a
    token is present.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(
        self, environ: Mapping[str, Any], start_response: Any
    ) -> Iterable[bytes]:
        process_request_queue_time(extract=lambda: request_start_from_environ(environ))
        return self.original_wsgi_app(environ, start_response)
