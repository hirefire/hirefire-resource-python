from hirefire_resource.middleware.asgi import RequestInfo, request

class Middleware:
    """
    Starlette (ASGI) middleware for autoscaling Heroku web and worker dynos using HireFire.

    This middleware interacts with the 'request' function (from hirefire_resource.middleware.asgi)
    to determine how to process each incoming HTTP request. The 'request' function is used to assess
    whether to respond with job queue metrics or pass the request to the next middleware.

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
        Asynchronous call method to process incoming requests.

        This method checks if the incoming request is an HTTP request and, if so, uses the 'request'
        function from hirefire_resource.middleware.asgi to decide the appropriate action based on
        the request information.

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

        This method prepares data, specifically the request path and start time, for the 'request'
        function, which is used to determine how to handle the incoming HTTP request.

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
