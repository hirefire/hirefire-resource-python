import json

from hirefire_resource import HireFire, __version__
from hirefire_resource.middleware import (  # noqa
    RequestInfo,
    matches_info_path,
    process_request_queue_time,
)


def request(request_info):
    process_request_queue_time(request_info)

    if matches_info_path(request_info):
        return construct_info_response()


def construct_info_response():
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "must-revalidate, private, max-age=0",
        "HireFire-Resource": f"Python-{__version__}",
    }
    body = json.dumps(collect_workers_data())
    return 200, headers, [body]


def collect_workers_data():
    return [
        {"name": worker.name, "value": worker.proc()}
        for worker in HireFire.configuration.workers
    ]
