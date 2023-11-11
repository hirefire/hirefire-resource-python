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

from hirefire_resource import HireFire
from hirefire_resource.middleware import (  # noqa
    RequestInfo,
    matches_info_path,
    process_request_queue_time,
)


def request(request_info):
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
    process_request_queue_time(request_info)

    if matches_info_path(request_info):
        return construct_info_response()


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
