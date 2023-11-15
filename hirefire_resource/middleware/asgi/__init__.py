import asyncio
import json

from hirefire_resource import HireFire
from hirefire_resource.middleware import (  # noqa
    RequestInfo,
    matches_info_path,
    process_request_queue_time,
)


async def request(request_info):
    """
    Framework-agnostic middleware integration function for autoscaling Heroku web and worker dynos.
    Works with normalized request data. It performs two key operations:

    1. Capturing and processing request queue time data and forwarding it to the `Web` instance.
    2. Responding to specific HTTP requests with JSON-formatted job queue metrics from `Worker` instances.

    The caller (frameworks-specific middleware) is responsible for extracting the request path and
    request start time from the request object and passing it to this function in a `RequestInfo`
    object.

    The function either returns a tuple of HTTP status, headers, and response body if the request
    matches the info path, or None if the request does not match and should proceed normally.

    Note that the 'HIREFIRE_TOKEN' environment variable is required to perform the above-mentioned
    operations.

    Args:
        request_info (RequestInfo): Object containing request details.

    Returns:
        tuple: A tuple of HTTP status, headers, and response body if the request matches the
                info path.  None if the request does not match and should proceed normally.
    """
    process_request_queue_time(request_info)

    if matches_info_path(request_info):
        return await construct_info_response()


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
