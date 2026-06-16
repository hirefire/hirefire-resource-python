# ASGI adapters: read X-Request-Start (or its X-Queue-Start synonym) from the
# scope, delegate to the shared process_request_queue_time, and pass the request
# through unchanged.


def request_start_from_scope(scope):
    # X-Queue-Start is an exact synonym for X-Request-Start (e.g. Render emits
    # it); prefer X-Request-Start when both are present, regardless of order.
    # Client-controlled bytes are decoded leniently so malformed input can't
    # raise here.
    queue_start = None
    for header_name, header_value in scope.get("headers", []):
        name = header_name.lower()
        if name == b"x-request-start":
            return header_value.decode("utf-8", "replace")
        if name == b"x-queue-start":
            queue_start = header_value.decode("utf-8", "replace")
    return queue_start
