import json
import os
import time

from hirefire_resource.middleware import NotConfigured


class BaseMiddleware:
    """
    Base middleware for capturing and providing metrics required for autoscaling
    Heroku web and worker dynos. It serves two primary roles:

    1. Responds to specific HTTP requests with JSON-formatted queue metrics.
    2. Captures and processes request queue time data from incoming HTTP requests,
       forwarding it to the HireFire web instance for dispatching.

    The middleware intercepts requests to the HireFire info endpoints and allows
    all other requests to pass through unaffected. The `HTTP_X_REQUEST_START` header,
    set by Heroku's routing layer, provides the data for measuring request queue times.

    Attributes:
        config (Configuration): Configuration object for HireFire operations, containing
                                Web and Worker object configurations.

    Raises:
        NotConfigured: If the configuration object is not configured.
    """

    def __init__(self, config):
        """
        Initializes the BaseMiddleware with the provided HireFire configuration.

        Args:
            config (Configuration): Configuration object

        Raises:
            NotConfigured: If no configuration is provided.
        """
        if not config:
            raise NotConfigured("No HireFire configuration provided.")
        self.config = config

    def process_request(self, request_info):
        """
        Process the incoming request and determine if it matches the HireFire info path. If it does,
        construct and return the HireFire info response. Otherwise, the request should continue through
        the middleware stack.

        In all cases, it'll process the request queue time and forward it to the HireFire web instance
        for dispatching, if configured.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            tuple: A tuple of HTTP status, headers, and response body if the request matches the info path.
                   None if the request does not match and should proceed normally.
        """
        self.process_request_queue_time(request_info)

        if self.matches_info_path(request_info):
            return self.construct_info_response()

    def matches_info_path(self, request_info):
        """
        Check if the request path matches the HireFire info path.

        The HIREFIRE_TOKEN environment variable is used to determine the info path.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            bool: True if the request matches the info path, False otherwise.
        """
        token = os.environ.get("HIREFIRE_TOKEN", "development")
        return request_info.path == f"/hirefire/{token}/info"

    def construct_info_response(self):
        """
        Construct a JSON response containing the current metrics for each configured worker.

        Returns:
            tuple: A tuple of HTTP status, headers, and JSON body containing the job queue metrics.
        """
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "must-revalidate, private, max-age=0",
        }
        body = json.dumps(
            [
                {"name": worker.name, "value": worker.proc()}
                for worker in self.config.workers
            ]
        )
        return 200, headers, [body]

    def process_request_queue_time(self, request_info):
        """
        Calculate the request queue time from the `HTTP_X_REQUEST_START` header and
        add it to the HireFire web instance's buffer for processing.

        It also ensures that the HireFire web instance is running, so that the request
        queue time information can be periodically dispatched to HireFire's servers.

        Args:
            request_info (RequestInfo): Object containing request details.
        """
        if not self.config.web:
            return

        if not request_info.request_start_time:
            return

        request_queue_time = self.calculate_request_queue_time(
            request_info.request_start_time
        )
        self.config.web.add_to_buffer(request_queue_time)
        self.config.web.start()

    def calculate_request_queue_time(self, request_start_time):
        """
        Calculate the time the request spent in the queue using the Heroku-specific header.

        Args:
            request_start_time (str): The timestamp when Heroku's routing layer first received the request.

        Returns:
            int: The time spent in the queue in milliseconds. If the calculated time is negative, it returns 0.
        """
        ms = int(time.time() * 1000) - int(request_start_time)
        return max(ms, 0)
