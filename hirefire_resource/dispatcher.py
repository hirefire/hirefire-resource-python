import json
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from hirefire_resource.buffer import FlushedBuffer
from hirefire_resource.client import Client, Response
from hirefire_resource.lease import Lease
from hirefire_resource.log import safe_log
from hirefire_resource.workers import Workers

if TYPE_CHECKING:
    import logging

    from hirefire_resource.buffer import Buffer
    from hirefire_resource.cpu import CPU
    from hirefire_resource.web import Web


class Dispatcher:
    WEB_BACKFILL_LIMIT = 60

    PAYLOAD_SIZE_LIMIT = 65_536

    DEFAULT_DISPATCH_FREQUENCY = 1
    MAX_DISPATCH_FREQUENCY = 30

    def __init__(
        self,
        web: "Web | None" = None,
        workers: Workers | None = None,
        cpu: "list[CPU] | None" = None,
        web_liveness: bool = True,
    ) -> None:
        self._web = web
        self._workers = workers if workers is not None else Workers()
        self._cpu: list[CPU] = cpu if cpu is not None else []
        self._web_liveness = web_liveness
        self._client = Client()
        self._lease = Lease(enabled=self._workers.any())
        self._mutex = threading.Lock()
        self._running = False
        self._pid: int | None = None
        self._thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._last_web_second: int | None = None
        self._dispatch_frequency = self.DEFAULT_DISPATCH_FREQUENCY
        self._next_dispatch_at: float | None = None

    def start(self) -> bool:
        if self._running and self._pid == os.getpid():
            return False

        try:
            with self._mutex:
                if self._running and self._pid == os.getpid():
                    return False

                thread = threading.Thread(target=self._dispatch_loop, daemon=True)
                worker_thread = (
                    threading.Thread(target=self._worker_loop, daemon=True)
                    if self._workers.any()
                    else None
                )
                thread.start()
                if worker_thread is not None:
                    worker_thread.start()
                self._thread = thread
                self._worker_thread = worker_thread
                self._running = True
                self._pid = os.getpid()
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Could not start dispatcher: {error}",
            )
            return False

        safe_log(self._logger(), "info", "[HireFire] Starting dispatcher.")

        return True

    def stop(self) -> bool:
        threads: list[threading.Thread] = []

        with self._mutex:
            if not self._running:
                return False

            self._running = False
            if self._pid == os.getpid():
                threads = [
                    thread
                    for thread in (self._thread, self._worker_thread)
                    if thread is not None
                ]
            self._thread = None
            self._worker_thread = None
            self._pid = None

        for thread in threads:
            thread.join(5)

        self._dispatch()

        self._client.close()
        self._lease.close()

        safe_log(self._logger(), "info", "[HireFire] Dispatcher stopped.")

        return True

    def running(self) -> bool:
        with self._mutex:
            return self._running and self._pid == os.getpid()

    def _reinit_after_fork(self) -> None:
        self._mutex = threading.Lock()
        self._client._reinit_after_fork()
        self._lease._reinit_after_fork()

    def _dispatch_loop(self) -> None:
        while self.running():
            self._tick()
            time.sleep(1)

    def _worker_loop(self) -> None:
        while self.running():
            self._worker_tick()
            time.sleep(1)

    def _tick(self) -> None:
        for collector in self._cpu:
            self._guard(collector.sample)
        self._dispatch_if_due()

    def _worker_tick(self) -> None:
        self._guard(self._lease.request_if_due)
        self._guard(lambda: self._lease.sample_if_due(self._workers.sample))

    def _dispatch_if_due(self) -> None:
        if (
            self._next_dispatch_at is not None
            and time.monotonic() < self._next_dispatch_at
        ):
            return

        self._dispatch()
        self._next_dispatch_at = time.monotonic() + self._dispatch_frequency

    def _guard(self, func: Callable[[], object]) -> None:
        try:
            func()
        except Exception as error:
            safe_log(self._logger(), "error", f"[HireFire] {error}")

    def _dispatch(self) -> None:
        data: FlushedBuffer | None = None
        try:
            data = self._buffer().flush()
            payload, watermark = self._build_payload(data)
            if not payload:
                return

            body = json.dumps(payload, separators=(",", ":"))
            if len(body.encode("utf-8")) > self.PAYLOAD_SIZE_LIMIT:
                return self._drop_oversized_payload(body, watermark)

            if os.environ.get("HIREFIRE_VERBOSE"):
                safe_log(
                    self._logger(), "info", f"[HireFire] Dispatching metrics: {body}"
                )
            response = self._client.submit_samples(body)
            self._apply_dispatch_frequency(response)
            if watermark is not None:
                self._last_web_second = watermark
        except Exception as error:
            if data and data["web"]:
                self._buffer().repopulate_web(data["web"])
            safe_log(self._logger(), "error", f"[HireFire] Dispatch error: {error}")

    def _apply_dispatch_frequency(self, response: Response | None) -> None:
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

    def _drop_oversized_payload(self, body: str, watermark: int | None) -> None:
        if watermark is not None:
            self._last_web_second = watermark
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] Dropped metrics payload: {len(body.encode('utf-8'))} bytes "
            f"exceeds the {self.PAYLOAD_SIZE_LIMIT}-byte limit. Resuming from the "
            "current second.",
        )

    def _build_payload(self, data: FlushedBuffer) -> tuple[list[Any], int | None]:
        entries: list[Any] = []
        watermark: int | None = None

        if self._web and self._web_liveness:
            samples = self._backfill_web_seconds(data["web"])
            watermark = max(samples.keys())
            entries.append(
                {"name": self._web.name, "samples": self._stringify_keys(samples)}
            )
        elif self._web and data["web"]:
            entries.append(
                {"name": self._web.name, "samples": self._stringify_keys(data["web"])}
            )

        entries.extend(data["workers"])

        for name, cpu_samples in data["cpu"].items():
            entries.append({"name": name, "samples": self._stringify_keys(cpu_samples)})

        return entries, watermark

    def _backfill_web_seconds(
        self, samples: dict[int, list[int]]
    ) -> dict[int, list[int]]:
        now = int(time.time())
        from_second = (
            self._last_web_second + 1 if self._last_web_second is not None else now
        )
        if from_second < now - self.WEB_BACKFILL_LIMIT:
            from_second = now - self.WEB_BACKFILL_LIMIT
        if from_second > now:
            from_second = now

        samples = dict(samples)
        for second in range(from_second, now + 1):
            samples.setdefault(second, [])
        return samples

    @staticmethod
    def _stringify_keys(samples: dict[int, list]) -> dict[str, list]:
        return {str(second): value for second, value in samples.items()}

    def _buffer(self) -> "Buffer":
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.buffer

    def _logger(self) -> "logging.Logger":
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.logger
