"""
@TODO incorporate into process_request?

Base middleware for capturing and providing metrics required for autoscaling Heroku web and
worker dynos. It serves two primary roles:

1. Responds to specific HTTP requests with JSON-formatted queue metrics.
2. Captures and processes request queue time data from incoming HTTP requests, forwarding it to
    the HireFire web instance for dispatching.

The middleware intercepts requests to the HireFire info endpoints and allows all other requests
to pass through unaffected. The `HTTP_X_REQUEST_START` header, set by Heroku's routing layer,
provides the data for measuring request queue times.

Attributes:
    config (Configuration): Configuration object for HireFire operations, containing Web and
                            Worker object configurations.
"""

import json
import os
import time

from hirefire_resource import HireFire


def process_request(request_info):
    """
    Process the incoming request and determine if it matches the HireFire info path. If it does,
    construct and return the HireFire info response. Otherwise, the request should continue
    through the middleware stack.

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

    process_request_queue_time(request_info)

    if matches_info_path(request_info):
        return construct_info_response()

def matches_info_path(request_info):
    """
    Check if the request path matches the HireFire info path.

    The HIREFIRE_TOKEN environment variable is used to determine the info path.

    Args:
        request_info (RequestInfo): Object containing request details.

    Returns:
        bool: True if the request matches the info path, False otherwise.
    """
    return request_info.path == f"/hirefire/{os.environ['HIREFIRE_TOKEN']}/info"

def construct_info_response():
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
            for worker in HireFire.configuration.workers
        ]
    )
    return 200, headers, [body]

def process_request_queue_time(request_info):
    """
    Calculate the request queue time from the `X-Request-Start` header and add it to the web
    instance's buffer for processing.

    It also ensures that the Web instance is running, so that the request queue time information
    can be periodically dispatched to HireFire's servers.

    Args:
        request_info (RequestInfo): Object containing request details.
    """
    if not HireFire.configuration.web:
        return

    if not request_info.request_start_time:
        return

    request_queue_time = calculate_request_queue_time(
        request_info.request_start_time
    )
    HireFire.configuration.web.add_to_buffer(request_queue_time)
    HireFire.configuration.web.start()

def calculate_request_queue_time(request_start_time):
    """
    Calculate the time the request spent in the queue using the Heroku-specific header.

    Args:
        request_start_time (str): The timestamp when Heroku's routing layer first received the request.

    Returns:
        int: The time spent in the queue in milliseconds. If the calculated time is negative, it returns 0.
    """
    ms = int(time.time() * 1000) - int(request_start_time)
    return max(ms, 0)
