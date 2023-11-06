from hirefire_resource.middleware import RequestInfo
from hirefire_resource.middleware.asgi import BaseMiddleware
from hirefire_resource.resource import Resource


class Middleware:
    """
    ASGI middleware for processing requests related to HireFire.

    This middleware integrates with Django's ASGI application to intercept requests
    and provide HireFire with the necessary information for scaling decisions.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request_info = RequestInfo(
                path=scope["path"],
                request_start_time=self.extract_request_start_time(scope),
            )
            middleware = BaseMiddleware(Resource.configuration)
            response_data = await middleware.process_request(request_info)
            if response_data:
                await self.send_response(send, response_data)
                return

        await self.inner(scope, receive, send)

    async def send_response(self, send, response_data):
        status, headers, body = response_data
        # Send HTTP response start event
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (k.encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()
                ],
            }
        )
        # Send HTTP response body event
        await send(
            {
                "type": "http.response.body",
                "body": body.encode("utf-8"),
            }
        )

    def extract_request_start_time(self, scope):
        headers = dict(scope["headers"])
        # The header 'x-request-start' is typically in the format 't=timestamp'
        request_start = headers.get(b"x-request-start")
        if request_start:
            try:
                return int(request_start.decode().strip("t="))
            except ValueError:
                pass
        return None
