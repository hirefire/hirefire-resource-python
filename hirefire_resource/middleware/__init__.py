import os
import time

from hirefire_resource import HireFire


class RequestInfo:
    def __init__(self, path, request_start_time=None, token=None):
        self.path = path

        if request_start_time:
            self.request_start_time = int(request_start_time)
        else:
            self.request_start_time = None

        self.token = token


def matches_hirefire_path(request_info):
    return (
        os.environ.get("HIREFIRE_TOKEN")
        and request_info.path == "/hirefire"
        and request_info.token == os.environ.get("HIREFIRE_TOKEN")
    )


def matches_info_path(request_info):
    return (
        os.environ.get("HIREFIRE_TOKEN")
        and request_info.path == f"/hirefire/{os.environ.get('HIREFIRE_TOKEN')}/info"
    )


def process_request_queue_time(request_info):
    if not (
        os.environ.get("HIREFIRE_TOKEN")
        and HireFire.configuration.web
        and request_info.request_start_time
    ):
        return

    request_queue_time = calculate_request_queue_time(request_info)

    HireFire.configuration.web.start_dispatcher()
    HireFire.configuration.web.add_to_buffer(request_queue_time)


def calculate_request_queue_time(request_info):
    return max(int(time.time() * 1000) - request_info.request_start_time, 0)
