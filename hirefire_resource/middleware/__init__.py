import os
import time

from hirefire_resource import HireFire


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


def matches_info_path(request_info):
    """
    Checks if the request path matches the HireFire info path.

    Args:
        request_info (RequestInfo): Object containing request details.

    Returns:
        bool: True if the request matches the info path, False otherwise.
    """
    return (
        os.environ.get("HIREFIRE_TOKEN")
        and request_info.path == f"/hirefire/{os.environ.get('HIREFIRE_TOKEN')}/info"
    )


def process_request_queue_time(request_info):
    """
    Asynchronously calculates and processes the request's time spent in the queue.

    Args:
        request_info (RequestInfo): Object containing request details.
    """
    if not (
        os.environ.get("HIREFIRE_TOKEN")
        and HireFire.configuration.web
        and request_info.request_start_time
    ):
        return

    request_queue_time = calculate_request_queue_time(request_info.request_start_time)

    HireFire.configuration.web.add_to_buffer(request_queue_time)
    HireFire.configuration.web.start()


def calculate_request_queue_time(request_start_time):
    """
    Calculates the time the request spent in the queue using the X-Request-Start header provide by
    Heroku's routing layer.

    Args:
        request_start_time (str): The timestamp when Heroku's routing layer first received the request.

    Returns:
        int: The time spent in the queue in milliseconds. If the calculated time is negative, it returns 0.
    """
    return max(int(time.time() * 1000) - int(request_start_time), 0)
