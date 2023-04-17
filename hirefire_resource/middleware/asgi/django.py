from hirefire_resource.middleware.asgi import RequestInfo, request


class HireFireMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            token = None
            for header_name, header_value in scope["headers"]:
                if header_name.lower() == b"hirefire-token":
                    token = header_value.decode("utf-8")
                    break

            response = await request(
                RequestInfo(
                    path=scope["path"],
                    request_start_time=self.extract_request_start_time(scope),
                    token=token,
                )
            )

            if response:
                await self.send_response(send, response)
                return

        await self.inner(scope, receive, send)

    async def send_response(self, send, response_data):
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
        for header_name, header_value in scope["headers"]:
            if header_name.lower() == b"x-request-start":
                timestamp_str = header_value.decode("utf-8")
                try:
                    return int(timestamp_str)
                except ValueError:
                    pass
        return None
