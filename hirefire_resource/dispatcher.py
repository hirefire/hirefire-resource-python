import json
import os
import threading
import time

from hirefire_resource.client import Client
from hirefire_resource.lease import Lease
from hirefire_resource.workers import Workers


class Dispatcher:
    # How far back (seconds) a dispatch may claim unreported web seconds. Matches
    # the server's ingest staleness acceptance and doubles as an honesty cap: a
    # process suspended longer than this must not assert liveness for that time.
    WEB_BACKFILL_LIMIT = 60

    # Mirrors the server's request body cap; larger payloads are rejected with 413.
    PAYLOAD_SIZE_LIMIT = 65_536

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

    # Fork-aware: a forked child inherits _running == True, but threads do not
    # survive fork, so "running" only counts in the process that started the
    # thread. In a child the pid check fails and start (called per request by the
    # middleware) creates a fresh thread for this process.
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
            # Only join a thread this process created; an inherited thread object
            # in a forked child references a thread that no longer exists.
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

    # Each stage is isolated: a failure in one (a lease renewal timing out, a job
    # sampler raising) must not starve the stages after it — most importantly
    # dispatch, which drains the buffer.
    def _tick(self):
        self._guard(self._lease.request_if_due)
        self._guard(lambda: self._lease.sample_if_due(self._workers.sample))
        for collector in self._cpu:
            self._guard(collector.sample)
        self._dispatch()

    def _guard(self, func):
        try:
            func()
        except Exception as error:
            self._logger().error(f"[HireFire] {error}")

    def _dispatch(self):
        data = self._buffer().flush()
        payload = self._build_payload(data)
        if not payload:
            return

        body = json.dumps(payload, separators=(",", ":"))
        if len(body.encode("utf-8")) > self.PAYLOAD_SIZE_LIMIT:
            return self._drop_oversized_payload(body)

        try:
            if os.environ.get("HIREFIRE_VERBOSE"):
                self._logger().info(f"[HireFire] Dispatching metrics: {body}")
            self._client.submit_samples(body)
            # Advance only after a successful submit so the next success re-claims
            # the seconds whose delivery failed; duplicate empty claims are
            # harmless server-side.
            if self._web_watermark is not None:
                self._last_web_second = self._web_watermark
        except Exception as error:
            if data and data["web"]:
                self._buffer().repopulate_web(data["web"])
            self._logger().error(f"[HireFire] Dispatch error: {error}")

    # Repopulating would retry the same oversized payload every tick, so it is
    # dropped outright. Advancing the watermark leaves the dropped seconds
    # unclaimed (missing data) rather than backfilled as empty (zero traffic).
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
            # Identity says this is not the http-serving process: real samples are
            # still delivered, but no liveness is synthesized — this process must
            # not claim the web name's seconds.
            entries.append(
                {"name": self._web.name, "samples": self._stringify_keys(data["web"])}
            )

        entries.extend(data["workers"])

        for name, samples in data["cpu"].items():
            entries.append({"name": name, "samples": self._stringify_keys(samples)})

        return entries

    # Claims every second since the last successfully dispatched one: seconds with
    # buffered samples keep them, seconds without get an explicit empty claim,
    # which the server reads as 0 traffic — so a delivery blip never leaves a gap
    # that an additive metric would misread as missing data. With no watermark
    # (first dispatch after boot) only the current second is claimed: a fresh
    # process must not assert liveness for time before it existed.
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
