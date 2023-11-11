import http.client
import json
import os
import threading
import time
from datetime import datetime


class Web:
    """
    Responsible for collecting and dispatching web metrics to the HireFire servers.

    This class is designed to function efficiently in both non-forked (single-process) and forked (multi-process)
    web server architectures. In a forked environment, it is recommended to instantiate Web objects within a
    server's initialization code to ensure that each process maintains its own dispatcher.

    The internal buffer (`_buffer`) is a critical component:
    - It's a dictionary where keys are timestamps (in seconds since the Epoch), and the values are lists of request queue
      time metrics recorded at that particular timestamp in milliseconds.
    - Metrics are batched together if added within the same second, enabling efficient dispatching to the HireFire server.
    - When metrics are dispatched, the entire buffer is flushed to prevent duplicate data transmission.
    - Metrics older than the BUFFER_TTL (Time-to-Live) will be discarded, ensuring that only recent and relevant metrics are sent.

    Example buffer contents:
        {
            1634367001: [3, 9],
            1634367002: [10, 12, 8],
        }

    Attributes:
        DISPATCH_INTERVAL (int): Interval between dispatch attempts in seconds.
        DISPATCH_TIMEOUT (int): Timeout for HTTP requests in seconds.
        BUFFER_TTL (int): Time-to-live for metrics in seconds. Metrics older than this value will be discarded.

    Raises:
        TokenNotFoundError: If the HIREFIRE_TOKEN environment variable is not set.
        NetworkError: If there's a network-related issue that prevents communication with the HireFire server.
        TimeoutError: If the request to the HireFire server times out.
        ServerError: If the HireFire server returns a 5xx status code indicating a server-side error.
    """

    DISPATCH_INTERVAL = 5  # Interval between dispatch attempts in seconds.
    DISPATCH_TIMEOUT = 5  # Timeout for HTTP requests in seconds.
    BUFFER_TTL = 60  # Time-to-live for metrics in seconds.

    class TokenNotFoundError(Exception):
        """Raised when the HIREFIRE_TOKEN environment variable is not found."""

    class NetworkError(Exception):
        """Raised when there's a network-related issue."""

    class TimeoutError(Exception):
        """Raised when the request to the server times out."""

    class ServerError(Exception):
        """Raised when the server returns a 5xx status."""

    def __init__(self):
        """
        Initializes the Web object with default values, preparing it to start collecting and dispatching metrics.

        The buffer is structured to group metrics by the second in which they were recorded, allowing
        efficient batch dispatching. The buffer will only retain recent metrics as defined by the BUFFER_TTL value.
        """
        self._buffer = {}  # Stores metrics grouped by timestamp of recording.
        self._mutex = threading.Lock()  # Ensures thread-safe access to the buffer.
        self._running = False  # Indicates if the dispatcher thread is running.
        self._thread = None  # Will hold the dispatcher thread once started.

    def start(self):
        """
        Starts the dispatcher in a separate thread to continuously dispatch web metrics to the HireFire servers.

        If the dispatcher is already running, this method will have no effect. After starting, the dispatcher
        logs an informational message indicating its state.

        Example:
            web = Web()
            web.start()
        """
        with self._mutex:
            if self._running:
                return
            self._running = True

        print("[HireFire] Starting web metrics dispatcher.")
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self):
        """
        Stops the dispatcher thread, ensuring that no further metrics are dispatched to the HireFire server.

        This method waits for the dispatcher's thread to complete before marking it as stopped.
        After stopping, the buffer is cleared and an informational message is logged.

        Example:
            web = Web()
            web.start()
            # ... some time later ...
            web.stop()
        """
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
        Adds a value to the buffer, associating it with the current timestamp.

        Args:
            value (int): The request queue time in milliseconds.

        Example:
            web = Web()
            web.add_to_buffer(150)
        """
        with self._mutex:
            timestamp = int(datetime.now().timestamp())
            self._buffer.setdefault(timestamp, []).append(value)

    def flush(self):
        """
        Flushes the buffer, returning its contents, and creates a new empty buffer.

        Returns:
            dict: The buffer's contents before the flush.
        """
        with self._mutex:
            buffer = self._buffer
            self._buffer = {}
            return buffer

    def dispatch(self):
        """
        Dispatches the metrics buffer to the HireFire servers.

        If the buffer is not empty, it submits the buffer contents. In case of an exception, it attempts to
        repopulate the buffer with the undelivered metrics.

        Note: This method does not return any value.
        """
        buffer = self.flush()
        if buffer:
            try:
                self._submit_buffer(buffer)
            except Exception as e:
                self._repopulate_buffer(buffer)
                print(f"[HireFire] Error while dispatching web metrics: {str(e)}")

    def _run(self):
        """
        The dispatcher's main loop that triggers the dispatch of metrics at set intervals.
        """
        while self.running():
            try:
                self.dispatch()
            except Exception as e:
                print(f"[HireFire] Unexpected error during dispatch: {str(e)}")
            time.sleep(self.DISPATCH_INTERVAL)

    def _repopulate_buffer(self, buffer):
        """
        Repopulates the buffer with valid metrics if a dispatch attempt fails.

        Args:
            buffer (dict): The buffer containing metrics to repopulate.
        """
        now = int(datetime.now().timestamp())
        with self._mutex:
            for timestamp, values in buffer.items():
                if timestamp >= now - self.BUFFER_TTL:
                    self._buffer.setdefault(timestamp, []).extend(values)

    def _submit_buffer(self, buffer):
        """
        Submits the buffer to the HireFire servers via an HTTP POST request.

        Args:
            buffer (dict): The buffer containing metrics to be dispatched.

        Returns:
            bool: True if the submission is successful, False otherwise.

        Raises:
            TokenNotFoundError: If the HIREFIRE_TOKEN environment variable is not set.
            NetworkError: For network-related issues.
            TimeoutError: If the request to the server times out.
            ServerError: If the server returns a 5xx status.
        """
        hirefire_token = os.environ.get("HIREFIRE_TOKEN")

        if not hirefire_token:
            raise self.TokenNotFoundError(
                "HIREFIRE_TOKEN environment variable is not set."
            )

        buffer_string = json.dumps(buffer)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "HireFire Agent (Python)",
            "HireFire-Token": hirefire_token,
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
