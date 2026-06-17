def request_start_from_environ(environ):
    # X-Queue-Start is an exact synonym for X-Request-Start (e.g. Render emits
    # it); prefer X-Request-Start when both are present.
    return environ.get("HTTP_X_REQUEST_START") or environ.get("HTTP_X_QUEUE_START")
