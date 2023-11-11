"""
@TODO incorporate into process_request?

Base ASGI middleware for capturing and providing metrics required for autoscaling
Heroku web and worker dynos. It serves two primary roles:

1. Responds to specific HTTP requests with JSON-formatted queue metrics.
2. Captures and processes request queue time data from incoming HTTP requests,
    forwarding it to the HireFire web instance for further handling or logging it for
    HireFire Logdrain capture, depending on configuration.

The middleware intercepts requests to the HireFire info endpoints and allows all other requests
to pass through unaffected. The `X-Request-Start` header, set by Heroku's routing layer,
provides the data for measuring request queue times.
"""

import asyncio
import json
import os
import time

from hirefire_resource import HireFire



async def process_request(request_info):
    """
    Asynchronously processes the incoming request and determines if it matches the HireFire info
    path. If it does, constructs and returns the HireFire info response. Otherwise, the request
    should continue through the middleware stack.

    If configured, the request queue time is calculated and added to the HireFire web instance's
    buffer for processing. The HireFire web instance is also started if it is not already
    running.

    Won't process the request if the `HIREFIRE_TOKEN` environment variable is not set.

    Args:
        request_info (RequestInfo): Object containing request details.

    Returns:
        tuple: A tuple of HTTP status, headers, and response body if the request matches the
                info path.  None if the request does not match and should proceed normally.
    """
    if not os.environ.get("HIREFIRE_TOKEN"):
        return

    await process_request_queue_time(request_info)

    if matches_info_path(request_info):
        return await construct_info_response()

def matches_info_path(request_info):
    """
    Checks if the request path matches the HireFire info path.

    Args:
        request_info (RequestInfo): Object containing request details.

    Returns:
        bool: True if the request matches the info path, False otherwise.
    """
    return request_info.path == f"/hirefire/{os.environ['HIREFIRE_TOKEN']}/info"

async def construct_info_response():
    """
    Asynchronously constructs a JSON response containing the current metrics for each configured worker.

    Returns:
        tuple: A tuple of HTTP status, headers, and JSON body containing the metrics.
    """
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "must-revalidate, private, max-age=0",
    }

    workers_info = await collect_workers_data()

    body = json.dumps(workers_info)
    return 200, headers, body

async def collect_workers_data():
    """
    Collects data from all workers asynchronously.

    Returns:
        list: A list of dictionaries with worker names and their respective values.
    """
    data = []
    for worker in HireFire.configuration.workers:
        result = worker.proc()
        if asyncio.iscoroutine(result):
            result = await result
        data.append({"name": worker.name, "value": result})
    return data

async def process_request_queue_time(request_info):
    """
    Asynchronously calculates and processes the request's time spent in the queue.

    Args:
        request_info (RequestInfo): Object containing request details.
    """
    if not (HireFire.configuration.web and request_info.request_start_time):
        return

    request_queue_time = calculate_request_queue_time(
        request_info.request_start_time
    )

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
    ms = int(time.time() * 1000) - int(request_start_time)
    return max(ms, 0)
