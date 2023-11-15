from hirefire_resource.middleware.asgi import RequestInfo, request


class Middleware:
    """
    Django (ASGI) middleware for autoscaling Heroku web and worker dynos using HireFire.

    This middleware interacts with the 'request' function (from hirefire_resource.middleware.asgi)
    to determine how to process each incoming HTTP request. The 'request' function is used to assess
    whether to respond with job queue metrics or pass the request to the next middleware.

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
        Asynchronous call method to process all incoming requests.

        This method checks if the incoming request is HTTP and, if so, uses the 'request' function
        to decide the appropriate action. See 'request' in hirefire_resource.middleware.asgi for
        more details on how the processing is determined.

        Args:
            scope (dict): The ASGI scope dictionary containing request details.
            receive (callable): An awaitable callable that yields the next event.
            send (callable): An awaitable callable to send events to the client.

        Returns:
            None: This method sends the response directly to the client using the 'send' callable.
        """
        if scope["type"] == "http":
            response = await request(
                RequestInfo(
                    path=scope["path"],
                    request_start_time=self.extract_request_start_time(scope),
                )
            )

            if response:
                await self.send_response(send, response)
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
        response_headers = [
            (key.encode("utf-8"), value.encode("utf-8"))
            for key, value in headers.items()
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

        This method prepares data, specifically the request start time, for the 'request' function,
        which is used to determine how to handle the incoming HTTP request.

        Args:
            scope (dict): The ASGI scope containing request details.

        Returns:
            int: The request start time in milliseconds if present and valid, otherwise None.
        """
        for header_name, header_value in scope["headers"]:
            if header_name.lower() == b"x-request-start":
                timestamp_str = header_value.decode("utf-8")
                try:
                    return int(timestamp_str)
                except ValueError:
                    pass
        return None
