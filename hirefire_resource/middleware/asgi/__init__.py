import asyncio
import json
import os
import time

from hirefire_resource.middleware import NotConfigured


class BaseMiddleware:
    """
    Base ASGI middleware for capturing and providing metrics required for autoscaling
    Heroku web and worker dynos. It serves two primary roles:

    1. Responds to specific HTTP requests with JSON-formatted queue metrics.
    2. Captures and processes request queue time data from incoming HTTP requests,
       forwarding it to the HireFire web instance for further handling or logging it for
       HireFire Logdrain capture, depending on configuration.

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
        Initializes the ASGI BaseMiddleware with the provided HireFire configuration.

        Args:
            config (Configuration): Configuration object.

        Raises:
            NotConfigured: If no configuration is provided.
        """
        if not config:
            raise NotConfigured("No HireFire configuration provided.")
        self.config = config

    async def process_request(self, request_info):
        """
        Asynchronously processes the incoming request and determines if it matches the HireFire info path. If it does,
        constructs and returns the HireFire info response. Otherwise, the request should continue through the middleware stack.

        If configured, the request queue time is calculated and added to the HireFire web instance's buffer
        for processing. The HireFire web instance is also started if it is not already running.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            tuple: A tuple of HTTP status, headers, and response body if the request matches the info path.
                   None if the request does not match and should proceed normally.
        """
        await self.process_request_queue_time(request_info)

        if self.matches_info_path(request_info):
            return await self.construct_info_response()

    def matches_info_path(self, request_info):
        """
        Checks if the request path matches the HireFire info path.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            bool: True if the request matches the info path, False otherwise.
        """
        token = os.environ.get("HIREFIRE_TOKEN", "development")
        return request_info.path == f"/hirefire/{token}/info"

    async def construct_info_response(self):
        """
        Asynchronously constructs a JSON response containing the current metrics for each configured worker.

        Returns:
            tuple: A tuple of HTTP status, headers, and JSON body containing the metrics.
        """
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "must-revalidate, private, max-age=0",
        }

        workers_info = await self.collect_workers_data()

        body = json.dumps(workers_info)
        return 200, headers, body

    async def collect_workers_data(self):
        """
        Collects data from all workers asynchronously.

        Returns:
            list: A list of dictionaries with worker names and their respective values.
        """
        data = []
        for worker in self.config.workers:
            result = worker.proc()
            if asyncio.iscoroutine(result):
                result = await result
            data.append({"name": worker.name, "value": result})
        return data

    async def process_request_queue_time(self, request_info):
        """
        Asynchronously calculates and processes the request's time spent in the queue.

        Args:
            request_info (RequestInfo): Object containing request details.
        """
        if not (self.config.web and request_info.request_start_time):
            return

        request_queue_time = self.calculate_request_queue_time(
            request_info.request_start_time
        )

        self.config.web.add_to_buffer(request_queue_time)
        if not self.config.web.running():
            self.config.web.start()

    def calculate_request_queue_time(self, request_start_time):
        """
        Calculates the time the request spent in the queue using the X-Request-Start header provide by
        Heroku's routing layer.

        Args:
            request_start_time (str): The timestamp when Heroku's routing layer first received the request.

        Returns:
            int: The time spent in the queue in milliseconds. If the calculated time is negative, it returns 0.
        """
        ms = int(time.time() * 1000) - int(request_start_time)
        return max(ms, 0)
