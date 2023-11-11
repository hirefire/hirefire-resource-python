from hirefire_resource.middleware import RequestInfo
from hirefire_resource.middleware.asgi import process_request


class Middleware:
    """
    Quart middleware for processing requests related to HireFire in an ASGI environment.

    This middleware serves as a bridge between Quart's asynchronous request/response flow and the
    HireFire base ASGI middleware. It standardizes incoming request information for the base middleware,
    which then decides whether to respond with HireFire metrics or to pass the request along for normal
    Quart processing.

    Attributes:
        app (Quart): The Quart application instance.
        original_app (callable): The original ASGI app callable that this middleware wraps.
    """

    def __init__(self, app):
        """
        Initializes the middleware with the provided Quart application instance.

        Args:
            app (Quart): The Quart application instance.
        """
        self.original_app = app.asgi_app

    async def __call__(self, scope, receive, send):
        """
        Asynchronous call method to process all incoming requests to the Quart application.

        Args:
            scope (dict): The ASGI scope dictionary containing request details.
            receive (callable): An awaitable callable that yields the next event.
            send (callable): An awaitable callable to send events to the client.

        Returns:
            None: This method does not return but instead sends the response using the 'send' callable.
        """
        if scope["type"] == "http":
            request_info = RequestInfo(
                path=scope["path"],
                request_start_time=self.extract_request_start_time(scope),
            )

            response_data = await process_request(request_info)

            if response_data:
                await self.send_response(send, *response_data)
                return

        await self.original_app(scope, receive, send)

    async def send_response(self, send, status, headers, body):
        """
        Sends an HTTP response back to the client through the ASGI 'send' callable.

        Args:
            send (callable): An awaitable callable to send events to the client.
            status (int): The HTTP status code for the response.
            headers (dict): The HTTP headers for the response.
            body (str): The body of the response.
        """
        response_headers = [
            (k.encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()
        ]
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": response_headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body.encode("utf-8"),
            }
        )

    def extract_request_start_time(self, scope):
        """
        Extracts the request start time from the ASGI scope, which contains HTTP headers.

        Args:
            scope (dict): The ASGI scope containing request details.

        Returns:
            int: The request start time in milliseconds if found, otherwise None.
        """
        for header_name, header_value in scope["headers"]:
            if header_name == b"x-request-start":
                return int(header_value.decode("utf-8"))
        return None
