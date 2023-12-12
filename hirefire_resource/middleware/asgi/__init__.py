import asyncio
import json

from hirefire_resource import HireFire
from hirefire_resource.middleware import (  # noqa
    RequestInfo,
    matches_hirefire_path,
    matches_info_path,
    process_request_queue_time,
)
from hirefire_resource.version import VERSION


async def request(request_info):
    process_request_queue_time(request_info)

    if matches_hirefire_path(request_info) or matches_info_path(request_info):
        return await construct_info_response()


async def construct_info_response():
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "must-revalidate, private, max-age=0",
        "HireFire-Resource": f"Python-{VERSION}",
    }
    workers_info = await collect_workers_data()
    body = json.dumps(workers_info)

    return 200, headers, body


async def collect_workers_data():
    data = []

    for worker in HireFire.configuration.workers:
        value = worker.value()
        if asyncio.iscoroutine(value):
            value = await value
        data.append({"name": worker.name, "value": value})

    return data
