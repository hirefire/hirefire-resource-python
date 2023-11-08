from hirefire_resource.middleware import RequestInfo
from hirefire_resource.middleware.asgi import BaseMiddleware
from hirefire_resource.resource import Resource


class Middleware:
    """
    ASGI middleware for processing requests related to HireFire within Django's ASGI application.

    This middleware captures incoming requests and determines if they are intended for HireFire,
    in which case it responds with appropriate metrics. Otherwise, it allows the Django application
    to handle the request as usual. It's designed to integrate with Django's ASGI lifecycle and should
    be placed early in the middleware stack to ensure accurate request queue time measurement.

    Attributes:
        inner (callable): The inner application or middleware wrapped by this middleware.
    """

    def __init__(self, inner):
        """
        Initializes the middleware with the next ASGI application or middleware in the stack.

        Args:
            inner (callable): The inner ASGI application or middleware.
        """
        self.inner = inner

    async def __call__(self, scope, receive, send):
        """
        Asynchronous call method to process all incoming ASGI requests.

        Extracts the necessary request information and determines if the request is for the
        HireFire info path. If it is, the middleware constructs and returns a response with
        the HireFire metrics. Otherwise, the request is passed on to the next application or
        middleware.

        Args:
            scope (dict): The ASGI scope dictionary containing request details.
            receive (callable): An awaitable callable that yields the next event.
            send (callable): An awaitable callable to send events to the client.

        Returns:
            None: This method sends the response directly to the client using the 'send' callable.
        """
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
        """
        Sends the ASGI HTTP response using the provided 'send' callable.

        Args:
            send (callable): An awaitable callable to send events to the client.
            response_data (tuple): A tuple containing the response status, headers, and body.
        """
        status, headers, body = response_data
        response_headers = [(key.encode('utf-8'), value.encode('utf-8')) for key, value in headers.items()]

        await send({
            'type': 'http.response.start',
            'status': status,
            'headers': response_headers,
        })
        await send({
            'type': 'http.response.body',
            'body': body.encode('utf-8'),
        })

    def extract_request_start_time(self, scope):
        """
        Extracts the request start time from the ASGI scope, which contains HTTP headers.

        Args:
            scope (dict): The ASGI scope containing request details.

        Returns:
            int: The request start time in milliseconds if present and valid, otherwise None.
        """
        for header_name, header_value in scope["headers"]:
            if header_name.lower() == b"x-request-start":
                timestamp_str = header_value.decode('utf-8')
                try:
                    return int(timestamp_str)
                except ValueError:
                    # If the header is not in the expected format, ignore it and continue
                    pass
        return None
