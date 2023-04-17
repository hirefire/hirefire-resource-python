from hirefire_resource.middleware.asgi import RequestInfo, request


class HireFireMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
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
        path = scope.get("path", "")
        headers = dict(scope.get("headers", []))
        request_start = headers.get(b"x-request-start")
        request_start_time = request_start.decode() if request_start else None
        hirefire_token = headers.get(b"hirefire-token")
        hirefire_token = hirefire_token.decode() if hirefire_token else None
        return RequestInfo(path, request_start_time, hirefire_token)

    @staticmethod
    async def send_response(send, response):
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
