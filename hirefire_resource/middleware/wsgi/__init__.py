# WSGI adapters: read X-Request-Start (or its X-Queue-Start synonym) from the
# environ, delegate to the shared process_request_queue_time, and pass the
# request through unchanged.


def request_start_from_environ(environ):
    # X-Queue-Start is an exact synonym for X-Request-Start (e.g. Render emits
    # it); prefer X-Request-Start when both are present.
    return environ.get("HTTP_X_REQUEST_START") or environ.get("HTTP_X_QUEUE_START")
