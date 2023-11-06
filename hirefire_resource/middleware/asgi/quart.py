from hirefire_resource.middleware import RequestInfo
from hirefire_resource.middleware.asgi import BaseMiddleware
from hirefire_resource.resource import Resource


class Middleware:
    """
    Quart middleware for processing requests related to HireFire.

    This middleware works similarly to the Flask middleware but is designed
    for the asynchronous nature of Quart and conforms to the ASGI specification.
    It extracts request information and forwards it to the base HireFire ASGI middleware.
    If the request is related to HireFire, it constructs an appropriate response.
    Otherwise, it passes on the request for further processing.
    """

    def __init__(self, app):
        """
        Initialize the middleware with a given Quart application instance.

        Args:
            app (Quart): The Quart application instance.
        """
        # self.app = app
        self.app = app
        self.original_app = app.asgi_app  # Store the original ASGI app here

    async def __call__(self, scope, receive, send):
        """
        Process the incoming ASGI request.

        This method is an entry point for the ASGI application, conforming to the
        ASGI specification. It checks if a request is meant for HireFire and processes it,
        otherwise it lets Quart handle the request.

        Args:
            scope (dict): The ASGI scope dictionary containing request details.
            receive (callable): An awaitable callable that yields the next event.
            send (callable): An awaitable callable to send events to the client.

        Returns:
            None: This method does not return but sends the response using the 'send' callable.
        """
        if scope["type"] == "http":
            # Construct RequestInfo from ASGI scope
            request_info = RequestInfo(
                path=scope["path"],
                request_start_time=self.extract_request_start_time(scope),
            )

            # Process request using HireFire ASGI middleware
            middleware = BaseMiddleware(Resource.configuration)
            response_data = await middleware.process_request(request_info)

            if isinstance(response_data, tuple):
                # If response_data is provided by HireFire middleware, send this response back to ASGI
                status, headers, body = response_data

                await self.send_response(send, status, headers, body)

                return

        # If not a HireFire request, just call the actual Quart app
        # await self.app(scope, receive, send)
        await self.original_app(scope, receive, send)

    async def send_response(self, send, status, headers, body):
        """
        Send the ASGI response using the provided 'send' callable.

        Args:
            send (callable): An awaitable callable to send events to the client.
            status (int): The HTTP status code.
            headers (dict): The response headers.
            body (str): The response body.
        """
        response_headers = [
            (key.encode(), value.encode()) for key, value in headers.items()
        ]

        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": response_headers,
            }
        )

        await send({"type": "http.response.body", "body": body.encode()})

    def extract_request_start_time(self, scope):
        """
        Extract the request start time from ASGI scope headers.

        Args:
            scope (dict): The ASGI scope dictionary containing request details.

        Returns:
            int: The request start time in milliseconds.
        """

        for header in scope["headers"]:
            if header[0] == b"x-request-start":
                return int(header[1])

        return None
