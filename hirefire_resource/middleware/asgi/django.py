from hirefire_resource.middleware import process_request_queue_time
from hirefire_resource.middleware.asgi import request_start_from_scope


class HireFireMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            process_request_queue_time(request_start_from_scope(scope))
        await self.inner(scope, receive, send)
