# ASGI middleware adapters (Starlette/FastAPI, Django ASGI, Quart). Each adapter
# reads the X-Request-Start header from the ASGI scope and delegates to the shared
# hirefire_resource.middleware.process_request_queue_time, then passes the request
# through unchanged. The push model serves no inline endpoints.


def request_start_from_scope(scope):
    for header_name, header_value in scope.get("headers", []):
        if header_name.lower() == b"x-request-start":
            return header_value.decode("utf-8")
    return None
