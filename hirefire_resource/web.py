import http.client
import json
import os
import threading
import time
from datetime import datetime
from hirefire_resource import __version__


class Web:
    """
    Handles the collection and dispatch of web metrics to HireFire's servers.

    This class is designed to operate efficiently in various web server architectures, including
    both non-forked (single-process) and forked (multi-process) server models.  It is thread-safe,
    making it suitable for use with multithreaded servers.  In forked environments, each worker
    process should have its own instance of this class.

    Attributes:
        DISPATCH_INTERVAL (int): Interval between dispatch attempts in seconds.
        DISPATCH_TIMEOUT (int): Timeout for HTTP requests in seconds.
        BUFFER_TTL (int): Time-to-live for metrics in the buffer in seconds.

    Raises:
        NetworkError: For network-related issues.
        TimeoutError: When a request to the server times out.
        ServerError: When the server returns a 5xx status.
    """

    DISPATCH_INTERVAL = 5  # Interval between dispatch attempts in seconds.
    DISPATCH_TIMEOUT = 5  # Timeout for HTTP requests in seconds.
    BUFFER_TTL = 60  # Time-to-live for metrics in seconds.

    class NetworkError(Exception):
        """Raised for network-related issues."""

    class TimeoutError(Exception):
        """Raised when the request to the server times out."""

    class ServerError(Exception):
        """Raised when the server returns a 5xx status."""

    def __init__(self):
        """Initializes a new Web instance."""
        self._buffer = {}
        self._mutex = threading.Lock()
        self._running = False
        self._thread = None

    def start(self):
        """Starts the dispatcher in a separate thread to dispatch web metrics."""
        with self._mutex:
            if self._running:
                return
            self._running = True

        print("[HireFire] Starting web metrics dispatcher.")
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self):
        """Stops the dispatcher thread and clears the metric buffer."""
        with self._mutex:
            if not self._running:
                return
            self._running = False

        if self._thread:
            self._thread.join(self.DISPATCH_TIMEOUT)
            self._thread = None

        self._buffer.clear()
        print("[HireFire] Web metrics dispatcher stopped.")

    def running(self):
        """
        Checks if the dispatcher is currently running.

        Returns:
            bool: True if the dispatcher is running, False otherwise.
        """
        with self._mutex:
            return self._running

    def add_to_buffer(self, value):
        """
        Adds a value to the metric buffer.

        Args:
            value (int): The request queue time in milliseconds.
        """
        with self._mutex:
            timestamp = int(datetime.now().timestamp())
            self._buffer.setdefault(timestamp, []).append(value)

    def flush(self):
        """
        Flushes the current buffer, returning its contents.

        Returns:
            dict: The contents of the buffer before clearing.
        """
        with self._mutex:
            buffer = self._buffer
            self._buffer = {}
            return buffer

    def dispatch(self):
        """Dispatches the buffer contents to HireFire's servers."""
        buffer = self.flush()

        if buffer:
            try:
                self._submit_buffer(buffer)
            except Exception as e:
                self._repopulate_buffer(buffer)
                print(f"[HireFire] Error while dispatching web metrics: {str(e)}")

    def _run(self):
        """Runs the dispatcher loop in a separate thread."""
        while self.running():
            try:
                self.dispatch()
            except Exception as e:
                print(f"[HireFire] Unexpected error during dispatch: {str(e)}")
            time.sleep(self.DISPATCH_INTERVAL)

    def _repopulate_buffer(self, buffer):
        """Repopulates the buffer with given contents, filtering out old entries."""
        now = int(datetime.now().timestamp())
        with self._mutex:
            for timestamp, values in buffer.items():
                if timestamp >= now - self.BUFFER_TTL:
                    self._buffer.setdefault(timestamp, []).extend(values)

    def _submit_buffer(self, buffer):
        """Submits the buffer contents to HireFire's servers via an HTTP POST request."""
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
                raise self.ServerError(
                    f"Server returned {response.status}: {response.reason}"
                )
            else:
                raise self.NetworkError(
                    f"Request failed with {response.status}: {response.reason}"
                )

        except http.client.HTTPException as e:
            raise self.NetworkError(f"HTTP error occurred: {str(e)}")
        except socket.timeout:
            raise self.TimeoutError("The request to the server timed out.")
        except Exception as e:
            raise self.NetworkError(f"Unexpected error occurred: {str(e)}")
        finally:
            connection.close()
