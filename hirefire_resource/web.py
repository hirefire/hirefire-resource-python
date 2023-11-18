import http.client
import json
import logging
import os
import threading
import time
from datetime import datetime

from hirefire_resource import __version__


class Web:
    DISPATCH_INTERVAL = 5
    DISPATCH_TIMEOUT = 5
    BUFFER_TTL = 60

    def __init__(self):
        self._buffer = {}
        self._mutex = threading.Lock()
        self._running = False
        self._thread = None
        self.configuration = None

    @property
    def logger(self):
        if self.configuration:
            return self.configuration.logger
        else:
            return logging.getLogger()

    def start(self):
        with self._mutex:
            if self._running:
                return
            self._running = True

        self.logger.info("[HireFire] Starting web metrics dispatcher.")

        self._thread = threading.Thread(target=self._start)
        self._thread.start()

    def stop(self):
        with self._mutex:
            if not self._running:
                return
            self._running = False

        if self._thread:
            self._thread.join(self.DISPATCH_TIMEOUT)
            self._thread = None

        self.flush()

        self.logger.info("[HireFire] Web metrics dispatcher stopped.")

    def running(self):
        with self._mutex:
            return self._running

    def add_to_buffer(self, value):
        with self._mutex:
            timestamp = int(datetime.now().timestamp())
            self._buffer.setdefault(timestamp, []).append(value)

    def flush(self):
        with self._mutex:
            buffer = self._buffer
            self._buffer = {}
            return buffer

    def dispatch(self):
        buffer = self.flush()

        if buffer:
            try:
                self._submit_buffer(buffer)
            except Exception as e:
                self._repopulate_buffer(buffer)
                self.logger.error(
                    f"[HireFire] Error while dispatching web metrics: {str(e)}"
                )

    def _start(self):
        while self.running():
            self.dispatch()
            time.sleep(self.DISPATCH_INTERVAL)

    def _repopulate_buffer(self, buffer):
        now = int(datetime.now().timestamp())
        with self._mutex:
            for timestamp, values in buffer.items():
                if timestamp >= now - self.BUFFER_TTL:
                    self._buffer.setdefault(timestamp, []).extend(values)

    def _submit_buffer(self, buffer):
        buffer_string = json.dumps(buffer)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "HireFire Agent (Python)",
            "HireFire-Token": os.environ.get("HIREFIRE_TOKEN"),
            "HireFire-Resource": f"Python-{__version__}",
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
