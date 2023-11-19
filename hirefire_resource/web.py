import http.client
import json
import os
import threading
import time
from datetime import datetime

from hirefire_resource.version import VERSION


class Web:
    DISPATCH_INTERVAL = 5
    DISPATCH_TIMEOUT = 5
    BUFFER_TTL = 60

    def __init__(self, configuration):
        self._buffer = {}
        self._mutex = threading.Lock()
        self._dispatcher_running = False
        self._dispatcher = None
        self._configuration = configuration

    def start_dispatcher(self):
        with self._mutex:
            if self._dispatcher_running:
                return
            self._dispatcher_running = True

        self._logger.info("[HireFire] Starting web metrics dispatcher.")

        self._dispatcher = threading.Thread(target=self._start_dispatcher)
        self._dispatcher.start()

    def stop_dispatcher(self):
        with self._mutex:
            if not self._dispatcher_running:
                return
            self._dispatcher_running = False

        if self._dispatcher:
            self._dispatcher.join(self.DISPATCH_TIMEOUT)
            self._dispatcher = None

        self._flush_buffer()

        self._logger.info("[HireFire] Web metrics dispatcher stopped.")

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
                self._submit_buffer(buffer)
            except Exception as e:
                self._repopulate_buffer(buffer)
                self._logger.error(
                    f"[HireFire] Error while dispatching web metrics: {str(e)}"
                )

    def _start_dispatcher(self):
        while self.dispatcher_running():
            self._dispatch_buffer()
            time.sleep(self.DISPATCH_INTERVAL)

    def _repopulate_buffer(self, buffer):
        now = int(datetime.now().timestamp())
        with self._mutex:
            for timestamp, request_queue_times in buffer.items():
                if timestamp >= now - self.BUFFER_TTL:
                    self._buffer.setdefault(timestamp, []).extend(request_queue_times)

    def _submit_buffer(self, buffer):
        buffer_string = json.dumps(buffer)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "HireFire Agent (Python)",
            "HireFire-Token": os.environ.get("HIREFIRE_TOKEN"),
            "HireFire-Resource": f"Python-{VERSION}",
        }

        connection = http.client.HTTPSConnection(
            "logdrain.hirefire.io", timeout=self.DISPATCH_TIMEOUT
        )

        try:
            connection.request("POST", "/", buffer_string, headers)
            response = connection.getresponse()

            if 200 <= response.status < 300:
                return True
            elif 500 <= response.status < 600:
                raise Exception(f"Server returned {response.status}: {response.reason}")
            else:
                raise Exception(
                    f"Request failed with {response.status}: {response.reason}"
                )

        except http.client.HTTPException as e:
            raise Exception(f"HTTP error occurred: {str(e)}")
        except socket.timeout:
            raise Exception("The request to the server timed out.")
        except Exception as e:
            raise Exception(f"Unexpected error occurred: {str(e)}")
        finally:
            connection.close()

    @property
    def _logger(self):
        return self._configuration.logger
