import os
import time
import json

class NotConfigured(Exception):
    """Exception raised when the necessary configuration isn't provided."""
    pass

class RequestInfo:
    """
    Represents details of an HTTP request.

    Attributes:
        path (str): The request path.
        headers (dict): The request headers.
    """

    def __init__(self, path, headers):
        """
        Initialize RequestInfo with the given path and headers.

        Args:
            path (str): The request path.
            headers (dict): The request headers.
        """
        self.path = path
        self.headers = headers

class Middleware:
    """
    Middleware for processing requests related to HireFire.

    Attributes:
        config (obj): Configuration object for HireFire operations.
    """

    def __init__(self, config):
        """
        Initialize the middleware with the given configuration.

        Args:
            config (obj): Configuration object for HireFire.

        Raises:
            NotConfigured: If no configuration is provided.
        """
        if not config:
            raise NotConfigured("No HireFire configuration provided.")

        self.config = config

    def process_request(self, request_info):
        """
        Process the incoming request.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            tuple: HTTP status, headers, and body of the response if the request matches info path.
        """
        self.process_request_queue_time(request_info)
        if self.matches_info_path(request_info):
            return self.construct_info_response(request_info)

    def matches_info_path(self, request_info):
        """
        Determine if the request path matches the expected HireFire info path.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            bool: True if the request matches the info path, else False.
        """
        token = os.environ.get("HIREFIRE_TOKEN", "development")

        return request_info.path == f"/hirefire/{token}/info"

    def construct_info_response(self, request_info):
        """
        Construct the HireFire info response.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            tuple: HTTP status, headers, and body of the response.
        """
        headers = {
            "content-type": "application/json",
            "cache-control": "must-revalidate, private, max-age=0",
        }
        body = json.dumps([{"name": worker.name, "value": worker.proc()} for worker in self.config.workers])

        return 200, headers, body

    def process_request_queue_time(self, request_info):
        """
        Calculate and process the request's time spent in the queue.

        Args:
            request_info (RequestInfo): Object containing request details.
        """
        if not self.config.web:
            return

        token = os.environ.get("HIREFIRE_TOKEN")

        if not token:
            return

        timestamp = request_info.headers.get("HTTP_X_REQUEST_START")

        if not timestamp:
            return

        request_queue_time = self.calculate_request_queue_time(timestamp)

        self.config.web.add_to_buffer(request_queue_time)
        self.config.web.start()

    def calculate_request_queue_time(self, timestamp):
        """
        Calculate the request's time spent in the queue based on a timestamp.

        Args:
            timestamp (str): The timestamp from the request headers.

        Returns:
            int: Time spent in the queue in milliseconds.
        """
        ms = int(time.time() * 1000) - int(timestamp)
        return 0 if ms < 0 else ms
