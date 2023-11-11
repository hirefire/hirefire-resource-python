class RequestInfo:
    """
    Represents details of an HTTP request.

    Attributes:
        path (str): The request path.
        request_start_time (int, optional): The request start time in milliseconds.
    """

    def __init__(self, path, request_start_time=None):
        """
        Initialize RequestInfo with the given path and headers.

        Args:
            path (str): The request path.
            request_start_time (int, str, optional): The request start time in milliseconds.
        """
        self.path = path

        if request_start_time:
            self.request_start_time = int(request_start_time)
        else:
            self.request_start_time = None
