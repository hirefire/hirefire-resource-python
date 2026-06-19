import json
import os
import threading
import time

from hirefire_resource.client import Client
from hirefire_resource.lease import Lease
from hirefire_resource.workers import Workers


class Dispatcher:
    WEB_BACKFILL_LIMIT = 60

    # Mirrors the server's request body cap.
    PAYLOAD_SIZE_LIMIT = 65_536

    # Seconds between buffer dispatches; server-adjustable via the
    # HireFire-Dispatch-Frequency response header. Clamped to [1, 30].
    DEFAULT_DISPATCH_FREQUENCY = 1
    MAX_DISPATCH_FREQUENCY = 30

    def __init__(self, web=None, workers=None, cpu=None, web_liveness=True):
        self._web = web
        self._workers = workers if workers is not None else Workers()
        self._cpu = cpu if cpu is not None else []
        self._web_liveness = web_liveness
        self._client = Client()
        self._lease = Lease(enabled=self._workers.any())
        self._mutex = threading.Lock()
        self._running = False
        self._pid = None
        self._thread = None
        self._last_web_second = None
        self._web_watermark = None
        self._dispatch_frequency = self.DEFAULT_DISPATCH_FREQUENCY
        self._next_dispatch_at = None

    # Fork-aware: a child inherits _running but not the thread, so the pid check
    # forces the per-request start to spawn a fresh thread.
    def start(self):
        with self._mutex:
            if self._running and self._pid == os.getpid():
                return False

            self._running = True
            self._pid = os.getpid()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        self._logger().info("[HireFire] Starting dispatcher.")

        return True

    def stop(self):
        thread = None

        with self._mutex:
            if not self._running:
                return False

            self._running = False
            # Only join a thread this process created (a forked child's handle is dead).
            if self._pid == os.getpid():
                thread = self._thread
            self._thread = None
            self._pid = None

        if thread is not None:
            thread.join(5)

        self._dispatch()

        self._logger().info("[HireFire] Dispatcher stopped.")

        return True

    def running(self):
        with self._mutex:
            return self._running and self._pid == os.getpid()

    def _run(self):
        while self.running():
            self._tick()
            time.sleep(1)

    # Stage-isolated so one failure can't starve dispatch, which drains the buffer.
    # Sampling runs every tick; only dispatch is throttled.
    def _tick(self):
        self._guard(self._lease.request_if_due)
        self._guard(lambda: self._lease.sample_if_due(self._workers.sample))
        for collector in self._cpu:
            self._guard(collector.sample)
        self._dispatch_if_due()

    # First run dispatches immediately; the next time is set after dispatch so a
    # just-learned frequency applies next tick. A guarded dispatch never raises, so
    # a failure still waits a full window.
    def _dispatch_if_due(self):
        if self._next_dispatch_at is not None and time.time() < self._next_dispatch_at:
            return

        self._dispatch()
        self._next_dispatch_at = time.time() + self._dispatch_frequency

    def _guard(self, func):
        try:
            func()
        except Exception as error:
            self._logger().error(f"[HireFire] {error}")

    # Fully guarded: runs on the background thread (and once from stop), so any
    # failure must be logged, not propagated, or it kills the loop. data defaults to
    # None for the handler in case flush itself raised.
    def _dispatch(self):
        data = None
        try:
            data = self._buffer().flush()
            payload = self._build_payload(data)
            if not payload:
                return

            body = json.dumps(payload, separators=(",", ":"))
            if len(body.encode("utf-8")) > self.PAYLOAD_SIZE_LIMIT:
                return self._drop_oversized_payload(body)

            if os.environ.get("HIREFIRE_VERBOSE"):
                self._logger().info(f"[HireFire] Dispatching metrics: {body}")
            response = self._client.submit_samples(body)
            self._apply_dispatch_frequency(response)
            # Advance only after a successful submit; failed seconds re-claim next time.
            if self._web_watermark is not None:
                self._last_web_second = self._web_watermark
        except Exception as error:
            if data and data["web"]:
                self._buffer().repopulate_web(data["web"])
            self._logger().error(f"[HireFire] Dispatch error: {error}")

    # A non-positive or unparseable value keeps the prior frequency, so a bad
    # response can't collapse the interval and storm ingest. Clamp the rest; the
    # response is None on 401.
    def _apply_dispatch_frequency(self, response):
        if response is None:
            return

        raw = response.headers.get("HireFire-Dispatch-Frequency")
        if raw is None:
            return

        try:
            value = int(raw)
        except (TypeError, ValueError):
            return

        if value <= 0:
            return

        self._dispatch_frequency = min(value, self.MAX_DISPATCH_FREQUENCY)

    # Drop rather than repopulate (a retry would re-send the same oversized payload);
    # advancing the watermark leaves a gap instead of backfilling false zeros.
    def _drop_oversized_payload(self, body):
        if self._web_watermark is not None:
            self._last_web_second = self._web_watermark
        self._logger().error(
            f"[HireFire] Dropped metrics payload: {len(body.encode('utf-8'))} bytes "
            f"exceeds the {self.PAYLOAD_SIZE_LIMIT}-byte limit. Resuming from the "
            "current second."
        )

    def _build_payload(self, data):
        entries = []

        if self._web and self._web_liveness:
            samples = self._backfill_web_seconds(data["web"])
            self._web_watermark = max(samples.keys())
            entries.append(
                {"name": self._web.name, "samples": self._stringify_keys(samples)}
            )
        elif self._web and data["web"]:
            entries.append(
                {"name": self._web.name, "samples": self._stringify_keys(data["web"])}
            )

        entries.extend(data["workers"])

        for name, samples in data["cpu"].items():
            entries.append({"name": name, "samples": self._stringify_keys(samples)})

        return entries

    def _backfill_web_seconds(self, samples):
        now = int(time.time())
        from_second = (
            self._last_web_second + 1 if self._last_web_second is not None else now
        )
        if from_second < now - self.WEB_BACKFILL_LIMIT:
            from_second = now - self.WEB_BACKFILL_LIMIT
        if from_second > now:
            from_second = now

        samples = dict(samples)  # keep synthesized claims out of the retry buffer
        for second in range(from_second, now + 1):
            samples.setdefault(second, [])
        return samples

    @staticmethod
    def _stringify_keys(samples):
        return {str(second): value for second, value in samples.items()}

    def _buffer(self):
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.buffer

    def _logger(self):
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.logger
