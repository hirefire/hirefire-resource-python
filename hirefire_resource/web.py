import http.client
import json
import os
import re
import socket
import threading
import time
from datetime import datetime

from hirefire_resource.version import VERSION


class DispatchError(Exception):
    pass


class Web:
    def __init__(self, configuration):
        self._buffer = {}
        self._mutex = threading.Lock()
        self._dispatcher_running = False
        self._dispatcher = None
        self._dispatch_interval = 1
        self._dispatch_timeout = 5
        self._buffer_ttl = 60
        self._configuration = configuration

    def start_dispatcher(self):
        with self._mutex:
            if self._dispatcher_running:
                return False
            self._dispatcher_running = True

        self._logger.info("[HireFire] Starting web metrics dispatcher.")
        self._dispatcher = threading.Thread(target=self._start_dispatcher)
        self._dispatcher.start()
        return True

    def stop_dispatcher(self):
        with self._mutex:
            if not self._dispatcher_running:
                return False
            self._dispatcher_running = False

        if self._dispatcher:
            self._dispatcher.join(self._dispatch_timeout)
            self._dispatcher = None

        self._flush_buffer()
        self._logger.info("[HireFire] Web metrics dispatcher stopped.")
        return True

    def dispatcher_running(self):
        with self._mutex:
            return self._dispatcher_running

    def add_to_buffer(self, request_queue_time):
        with self._mutex:
            timestamp = int(datetime.now().timestamp())
            self._buffer.setdefault(timestamp, []).append(request_queue_time)

    def _flush_buffer(self):
        with self._mutex:
            buffer = self._buffer
            self._buffer = {}
            return buffer

    def _dispatch_buffer(self):
        buffer = self._flush_buffer()

        if buffer:
            try:
                if os.environ.get("HIREFIRE_VERBOSE"):
                    self._logger.info(f"[HireFire] Dispatching web metrics: {buffer}")
                self._submit_buffer(buffer)
            except Exception as e:
                self._repopulate_buffer(buffer)
                self._logger.error(
                    f"[HireFire] Error while dispatching web metrics: {str(e)}"
                )

    def _start_dispatcher(self):
        while self.dispatcher_running():
            self._dispatch_buffer()
            time.sleep(self._dispatch_interval)

    def _repopulate_buffer(self, buffer):
        now = int(datetime.now().timestamp())
        with self._mutex:
            for timestamp, request_queue_times in buffer.items():
                if timestamp >= now - self._buffer_ttl:
                    self._buffer.setdefault(timestamp, []).extend(request_queue_times)

    def _submit_buffer(self, buffer):
        hirefire_token = os.environ.get("HIREFIRE_TOKEN")

        if not hirefire_token:
            raise DispatchError(
                "The HIREFIRE_TOKEN environment variable is not set. Unable to submit "
                "Request Queue Time metric data. The HIREFIRE_TOKEN can be found in "
                "the HireFire Web UI in the web dyno manager settings."
            )

        buffer_string = json.dumps(buffer)

        headers = {
            "Content-Type": "application/json",
            "HireFire-Token": hirefire_token,
            "HireFire-Resource": f"Python-{VERSION}",
        }

        hirefire_dispatch_url = re.sub(
            r"^https?://",
            "",
            os.environ.get("HIREFIRE_DISPATCH_URL", "logdrain.hirefire.io"),
        )
        connection = http.client.HTTPSConnection(
            hirefire_dispatch_url, timeout=self._dispatch_timeout
        )

        try:
            connection.request("POST", "/", buffer_string, headers)
            response = connection.getresponse()

            if response.status >= 400:
                raise DispatchError(
                    f"HTTP error occurred: {response.status} {response.reason}"
                )

            self._adjust_parameters(response)
            return response
        except http.client.HTTPException as e:
            raise DispatchError(f"HTTP error occurred: {str(e)}")
        except socket.timeout:
            raise DispatchError("The request to the server timed out.")
        except Exception as e:
            raise DispatchError(f"Error occurred during request: {str(e)}")
        finally:
            connection.close()

    def _adjust_parameters(self, response):
        if "HireFire-Resource-Dispatch-Interval" in response.headers:
            self._dispatch_interval = int(
                response.headers["HireFire-Resource-Dispatch-Interval"]
            )
        if "HireFire-Resource-Dispatch-Timeout" in response.headers:
            self._dispatch_timeout = int(
                response.headers["HireFire-Resource-Dispatch-Timeout"]
            )
        if "HireFire-Resource-Buffer-TTL" in response.headers:
            self._buffer_ttl = int(response.headers["HireFire-Resource-Buffer-TTL"])

    @property
    def _logger(self):
        return self._configuration.logger
