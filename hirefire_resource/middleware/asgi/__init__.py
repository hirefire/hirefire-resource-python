import asyncio
import json
import os
import time

from hirefire_resource.middleware import NotConfigured


class BaseMiddleware:
    """
    Async middleware for processing requests related to HireFire.

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

    async def process_request(self, request_info):
        """
        Process the incoming request asynchronously.

        Args:
            request_info (RequestInfo): Object containing request details.

        Returns:
            tuple: HTTP status, headers, and body of the response if the request matches info path.
        """
        await self.process_request_queue_time(request_info)

        if self.matches_info_path(request_info):
            return await self.construct_info_response()

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

    async def construct_info_response(self):
        """
        Asynchronously construct the HireFire info response.

        Returns:
            tuple: HTTP status, headers, and body of the response.
        """
        headers = {
            "content-type": "application/json",
            "cache-control": "must-revalidate, private, max-age=0",
        }

        workers_info = []
        for worker in self.config.workers:
            proc_result = worker.proc()
            if asyncio.iscoroutine(proc_result):
                proc_result = await proc_result
            workers_info.append({"name": worker.name, "value": proc_result})

        body = json.dumps(workers_info)

        return 200, headers, body

    async def process_request_queue_time(self, request_info):
        """
        Asynchronously calculate and process the request's time spent in the queue.

        Args:
            request_info (RequestInfo): Object containing request details.
        """
        if not self.config.web:
            return

        token = os.environ.get("HIREFIRE_TOKEN")

        if not token:
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
        Calculate the request's time spent in the queue based on a timestamp.

        Args:
            request_start_time (int): The timestamp from the request headers.

        Returns:
            int: Time spent in the queue in milliseconds.
        """
        ms = int(time.time() * 1000) - request_start_time
        return 0 if ms < 0 else ms
