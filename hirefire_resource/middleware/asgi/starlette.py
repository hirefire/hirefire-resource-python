from hirefire_resource.middleware.asgi import RequestInfo, request


class Middleware:
    """
    ASGI middleware for processing requests related to HireFire in a Starlette application.

    This middleware intercepts incoming requests and checks if they are targeting the HireFire
    info endpoint. If so, it gathers the required metrics and constructs a response. For all
    other requests, it passes control to the next item in the ASGI application chain, allowing
    for standard processing within the Starlette framework.

    Attributes:
        app (ASGI application): The ASGI application instance that this middleware wraps.
    """

    def __init__(self, app):
        """
        Initializes the middleware with the ASGI application instance.

        Args:
            app (ASGI application): The ASGI application instance to wrap with HireFire middleware.
        """
        self.app = app

    async def __call__(self, scope, receive, send):
        """
        Asynchronous call method to process incoming requests via the ASGI interface.

        This method is the entry point for handling requests within the ASGI application. It
        extracts the necessary information from the ASGI scope, determines if the request is
        for the HireFire info path, and processes it accordingly. If not, it delegates to the
        wrapped ASGI application for regular processing.

        Args:
            scope (dict): The ASGI scope dictionary containing request details.
            receive (callable): The ASGI receive callable.
            send (callable): The ASGI send callable.

        Returns:
            None: Directly sends the response using the 'send' callable or passes the request
                  to the wrapped ASGI application.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response = await request(self.extract_request_info(scope))

        if response:
            await self.send_response(send, response)
        else:
            await self.app(scope, receive, send)

    @staticmethod
    def extract_request_info(scope):
        """
        Extracts the request start time and path from the ASGI scope.

        Args:
            scope (dict): The ASGI scope dictionary containing request headers.

        Returns:
            RequestInfo: An instance containing the path and request start time.
        """
        path = scope.get("path", "")
        headers = dict(scope.get("headers", []))
        request_start = headers.get(b"x-request-start")
        request_start_time = request_start.decode() if request_start else None
        return RequestInfo(path, request_start_time)

    @staticmethod
    async def send_response(send, response):
        """
        Sends the response data back to the client using the ASGI 'send' callable.

        Args:
            send (callable): The ASGI send callable.
            response (tuple): A tuple containing the response status code, headers, and body.
        """
        status, headers, body = response

        headers = [(k.encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()]

        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body.encode("utf-8"),
            }
        )
