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
            raise NotConfigured("No Autoscale configuration provided.")

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
        web_dispatcher = self.config.web_dispatcher

        if not web_dispatcher:
            return

        request_start_header = self.request_start_header(request_info)

        if not request_start_header:
            return

        current_time = int(time.time() * 1000)
        request_start_time = self.to_ms(request_start_header)
        elapsed_ms = current_time - request_start_time
        elapsed = max(0, elapsed_ms)
        web_dispatcher.add(elapsed)
        web_dispatcher.run()

    def request_start_header(self, request_info):
        return int(
            request_info.headers.get("HTTP_X_REQUEST_START")
            or request_info.headers.get("HTTP_X_QUEUE_START")
            or 0
        )

    def to_ms(self, start):
        if self.config.platform == "render":
            return int(start / 1000)
        return start
