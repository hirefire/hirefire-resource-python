import http.client
import json
import sys
import threading
import time
import traceback

from autoscale_agent.util import dispatch


class WebDispatcher:
    DISPATCH_INTERVAL = 15
    TTL = 30

    def __init__(self, token):
        self.id = token[:7]
        self.token = token
        self._buffer = {}
        self._buffer_lock = threading.Lock()
        self._running = False
        self._running_lock = threading.Lock()

    def dispatch(self):
        payload = self.flush()

        if not payload:
            return

        body = json.dumps(payload)
        response = dispatch(body, self.token)

        if not response.status == http.client.OK:
            print(
                f"Autoscale[{self.id}][ERROR]: Failed to dispatch data ({response.status})"
            )
            self.revert(payload)

    def add(self, value, timestamp=None):
        with self._buffer_lock:
            self._add(value, timestamp)

    def _add(self, value, timestamp=None):
        now = int(time.time())
        timestamp = timestamp or now

        if timestamp in self._buffer:
            if value > self._buffer[timestamp]:
                self._buffer[timestamp] = float(value)
        else:
            if timestamp + self.TTL > now:
                self._buffer[timestamp] = float(value)

    def flush(self):
        with self._buffer_lock:
            payload = self._buffer
            self._buffer = {}
            return payload

    def revert(self, payload):
        with self._buffer_lock:
            for timestamp, value in payload.items():
                self._add(value, timestamp)

    def run(self):
        with self._running_lock:
            if not self._running:
                self._running = True
                threading.Thread(target=self._run_loop, daemon=True).start()

    def _run_loop(self):
        while True:
            time.sleep(self.DISPATCH_INTERVAL)

            try:
                self.dispatch()
                sys.stdout.flush()
            except Exception as e:
                print(
                    f"Autoscale[{self.id}][ERROR]: {type(e).__name__}\n{traceback.print_tb(e.__traceback__)}"
                )
