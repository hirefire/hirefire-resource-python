import json
import time
import threading
import http.client
from datetime import datetime
import os

class Web:
    """
    Responsible for collecting and dispatching request queue time metrics to the HireFire server.

    Attributes:
        DISPATCH_INTERVAL (int): The interval between dispatch attempts in seconds.
        TIMEOUT (int): The timeout for HTTP requests in seconds.
        TTL (int): Metrics older than this value (in seconds) will be discarded.
    """

    class NetworkError(Exception):
        """Raised when there's a network-related issue."""
        pass

    class TimeoutError(Exception):
        """Raised when the request to the server times out."""
        pass

    class ServerError(Exception):
        """Raised when the server returns a 5xx status."""
        pass

    # The interval between dispatch attempts in seconds.
    DISPATCH_INTERVAL = 5

    # The timeout for HTTP requests in seconds.
    TIMEOUT = 5

    # Metrics older than this value (in seconds) will be discarded.
    TTL = 60

    def __init__(self):
        """
        Initializes the Web object with default values.

        Attributes:
            _buffer (dict): A buffer storing timestamps and request metrics.
            _mutex (threading.Lock): A lock for thread safety.
            _running (bool): Indicates if the dispatcher thread is running.
        """
        # buffer is a dict where the keys are timestamps (in seconds
        # since the Epoch) and the values are arrays of request queue
        # time metrics that have been added at that particular timestamp
        # on a per-request basis.
        #
        # For instance:
        # {
        #   1634367001 => [3, 9],
        #   1634367002 => [10, 12, 8]
        # }
        #
        # The purpose of this structure is to batch metrics added at the
        # same second together, allowing for more efficient dispatching
        # to the HireFire server.
        #
        # When metrics are dispatched to the server, the entire buffer
        # is flushed, ensuring that data isn't sent more than
        # once. Metrics older than a certain time (as defined by the TTL
        # constant) will be discarded if not dispatched in time,
        # ensuring that only recent and relevant metrics are sent.
        self._buffer = {}
        self._mutex = threading.Lock()
        self._running = False

    def start(self):
        """Starts the web metrics dispatcher thread."""
        with self._mutex:
            if self._running:
                return
            self._running = True
        print("[HireFire] Starting web metrics dispatcher.")
        self.thread = threading.Thread(target=self._run)
        self.thread.start()

    def stop(self):
        """Stops the web metrics dispatcher thread."""
        with self._mutex:
            if not self._running:
                return
            self._running = False
        self.thread.join(self.TIMEOUT)
        print("[HireFire] Web metrics dispatcher stopped.")

    def running(self):
        """
        Returns the running status of the web metrics dispatcher.

        Returns:
            bool: The current running status of the dispatcher.
        """
        with self._mutex:
            return self._running

    def add_to_buffer(self, value):
        """
        Adds a value to the buffer with the current timestamp.

        Args:
            value (int): The value to be added to the buffer.
        """
        with self._mutex:
            timestamp = int(datetime.now().timestamp())
            self._buffer.setdefault(timestamp, []).append(value)

    def flush(self):
        """
        Flushes the buffer and returns its content.

        Returns:
            dict: The current content of the buffer.
        """
        with self._mutex:
            buffer_copy = self._buffer.copy()
            self._buffer.clear()
            return buffer_copy

    def dispatch(self):
        """Dispatches the buffer to the server and handles any exceptions."""
        buffer = self.flush()
        if buffer:
            try:
                self._submit_buffer(buffer)
            except Exception as e:
                self._repopulate_buffer(buffer)
                print(f"[HireFire] Error while dispatching web metrics: {str(e)}")

    def _run(self):
        """
        The main loop for the dispatcher thread, dispatching metrics at regular intervals.
        """
        while self.running():
            try:
                self.dispatch()
            except Exception as e:
                print(f"[HireFire] Unexpected error during dispatch: {str(e)}")
                time.sleep(self.DISPATCH_INTERVAL)

    def _repopulate_buffer(self, buffer):
        """
        Repopulates the buffer with valid (non-expired) metrics if dispatching fails.

        Args:
            buffer (dict): The buffer containing metrics to be repopulated.
        """
        now = int(datetime.now().timestamp())
        with self._mutex:
            for timestamp, values in buffer.items():
                if timestamp >= now - self.TTL:
                    self._buffer.setdefault(timestamp, []).extend(values)

    def _submit_buffer(self, buffer):
        """
        Submits the buffer to the HireFire server.

        Args:
            buffer (dict): The buffer containing metrics to be dispatched.

        Returns:
            bool: True if the submission is successful, otherwise raises an error.
        """
        buffer_string = json.dumps(buffer)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "HireFire Agent (Python)",
            "HireFire-Token": os.environ.get("HIREFIRE_TOKEN", "")
        }
        connection = http.client.HTTPSConnection("logdrain.hirefire.io", timeout=self.TIMEOUT)

        try:
            connection.request("POST", "/", buffer_string, headers)
            response = connection.getresponse()

            if 200 <= response.status < 300:
                return True
            elif 500 <= response.status < 600:
                raise self.ServerError(f"Server returned {response.status}: {response.reason}")
            else:
                raise self.NetworkError(f"Request failed with {response.status}: {response.reason}")

        except http.client.HTTPException as e:
            raise self.NetworkError(f"HTTP error occurred: {str(e)}")
        except socket.timeout:
            raise self.TimeoutError("The request to the server timed out.")
        except Exception as e:
            raise self.NetworkError(f"Unexpected error occurred: {str(e)}")
        finally:
            connection.close()
