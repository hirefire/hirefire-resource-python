# @TODO continue here

import time


class NotConfigured(Exception):
    pass


class RequestInfo:
    def __init__(self, path, headers):
        self.path = path
        self.headers = headers


class Middleware:
    def __init__(self, config):
        if not config:
            raise NotConfigured("No HireFire configuration provided.")

        self.config = config

    def process_request(self, request_info):
        if request_info.path == "/autoscale":
            return self.serve(request_info)
        self.record_queue_time(request_info)

    def serve(self, request_info):
        tokens = request_info.headers.get("HTTP_AUTOSCALE_METRIC_TOKENS", "").split(",")
        server = self.config.worker_servers.find(tokens)
        if not server:
            return 404, {}, "Not Found"

        headers = {
            "content-type": "application/json",
            "cache-control": "must-revalidate, private, max-age=0",
        }
        body = server.serve()

        return 200, headers, body

    def record_queue_time(self, request_info):
        if not self.config.web:
            return

        request_start_header = self.request_start_header(request_info)

        if not request_start_header:
            return

        request_queue_time = self.calculate_request_queue_time(request_start_header)

        self.config.web.add(request_queue_time)
        self.config.web.run()

    def request_start_header(self, request_info):
        return int(
            request_info.headers.get("HTTP_X_REQUEST_START")
            or request_info.headers.get("HTTP_X_QUEUE_START")
            or 0
        )

    def calculate_request_queue_time(self, timestamp):
        ms = int(time.time() * 1000) - timestamp
        return 0 if ms < 0 else ms
