from typing import Any

from hirefire_resource.middleware import process_request_queue_time
from hirefire_resource.middleware.wsgi import request_start_from_environ


class HireFireMiddleware:
    """Django WSGI middleware that samples request queue time.

    Reads ``X-Request-Start`` (or ``X-Queue-Start``) from the request and records a
    queue-time sample when a token is present. Add it to ``MIDDLEWARE`` early in the
    stack.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        process_request_queue_time(
            extract=lambda: request_start_from_environ(request.META)
        )
        return self.get_response(request)
