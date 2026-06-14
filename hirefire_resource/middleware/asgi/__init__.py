# ASGI adapters: read X-Request-Start from the scope, delegate to the shared
# process_request_queue_time, and pass the request through unchanged.


def request_start_from_scope(scope):
    for header_name, header_value in scope.get("headers", []):
        if header_name.lower() == b"x-request-start":
            # Client-controlled bytes: decode leniently so malformed input can't
            # raise here.
            return header_value.decode("utf-8", "replace")
    return None
