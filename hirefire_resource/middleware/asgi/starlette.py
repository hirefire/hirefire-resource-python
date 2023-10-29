from hirefire_resource import Resource
from hirefire_resource.middleware import RequestInfo
from hirefire_resource.middleware.asgi import BaseMiddleware


class Middleware:
    """
    ASGI middleware for processing requests related to HireFire.

    Acts as a bridge between an ASGI application's request/response flow and the
    base HireFire middleware. It extracts request information from the ASGI scope and
    forwards it to the base middleware. If the request is relevant to HireFire,
    it constructs a response accordingly. Otherwise, it continues the regular request flow.

    Attributes:
        app (ASGI application): The original ASGI application function that this middleware wraps.
    """

    def __init__(self, app):
        """
        Initialize the middleware with a given ASGI application instance.

        Args:
            app (ASGI application): The original ASGI application function.
        """
        self.app = app

    async def __call__(self, scope, receive, send):
        """
        Process the incoming ASGI request.

        It extracts the request information, forwards it to the base HireFire middleware,
        and either sends a HireFire related response or proceeds with the regular
        request processing chain.

        Args:
            scope (dict): The ASGI scope dictionary.
            receive (function): The ASGI receive function.
            send (function): The ASGI send function.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        request_info = RequestInfo(path, headers.get("x_request_start"))
        middleware = BaseMiddleware(Resource.configuration)
        response_data = await middleware.process_request(request_info)

        if isinstance(response_data, tuple):
            status, headers_dict, body = response_data
            headers = [(k.encode(), v.encode()) for k, v in headers_dict.items()]
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body.encode()})
        else:
            await self.app(scope, receive, send)
