def request_start_from_environ(environ):
    return environ.get("HTTP_X_REQUEST_START") or environ.get("HTTP_X_QUEUE_START")
