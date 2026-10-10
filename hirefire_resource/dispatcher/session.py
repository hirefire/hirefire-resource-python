import json
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hirefire_resource import plan
from hirefire_resource.client import Client, Response
from hirefire_resource.dispatcher import payload
from hirefire_resource.failure_log import FailureLog
from hirefire_resource.lease import Lease
from hirefire_resource.log import format_error, safe_log, verbose
from hirefire_resource.once import Once
from hirefire_resource.sampler import Sampler
from hirefire_resource.strategy import RQT

if TYPE_CHECKING:
    import logging

    from hirefire_resource.buffer import Buffer
    from hirefire_resource.configuration import Configuration

Series = dict[str, dict[str, dict[int, Any]]]
Trace = dict[str, Any]


class Session:
    PAYLOAD_SIZE_LIMIT = 131_072
    DEFAULT_DISPATCH_FREQUENCY = 1
    MAX_DISPATCH_FREQUENCY = 30
    BACKOFF_DOUBLINGS = 5
    SAMPLE_ROUND_LIMIT = 60
    TICK = 1
    PREFORK_WAIT = 1

    def __init__(
        self, configuration: "Configuration", after: threading.Thread | None = None
    ) -> None:
        self._configuration = configuration
        self._predecessor = after
        self._client = Client(configuration)
        self._lease = Lease(configuration)
        self._mutex = threading.Lock()
        self._wake = threading.Condition(self._mutex)
        self._live = True
        self._handoff = False
        self._ended = False
        self._abandoned = False
        self._dispatch_thread: threading.Thread | None = None
        self._lease_thread: threading.Thread | None = None
        self.sample_thread: threading.Thread | None = None
        self._dispatch_frequency = self.DEFAULT_DISPATCH_FREQUENCY
        self._next_dispatch_at: float | None = None
        self._last_rqt_second: int | None = None
        self._pending_sample_trace: Trace | None = None
        self._round_started_at: float | None = None
        self._dispatch_failures = FailureLog("Dispatch", configuration)
        self._lease_failures = FailureLog("Lease request", configuration)
        self._payload_drops = FailureLog("Metrics payload", configuration)
        self._sampler = Sampler(configuration)
        self._once = Once(configuration)

    def start(self) -> "Session":
        self._dispatch_thread = self._spawn("hirefire-dispatch", self._dispatch_loop)
        return self

    def live(self) -> bool:
        return self._live

    def alive(self) -> bool:
        thread = self._dispatch_thread
        return self._live and thread is not None and thread.is_alive()

    def halt(self, handoff: bool = False) -> None:
        with self._mutex:
            self._live = False
            self._handoff = handoff
            self._wake.notify_all()

    def join(self, timeout: float) -> bool:
        thread = self._dispatch_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def close(self) -> None:
        self._client.close()

    def abandon(self) -> None:
        with self._mutex:
            self._abandoned = True
            ended = self._ended
        if ended:
            self.close()

    def discard_inherited(self) -> None:
        self._live = False
        self._client.discard_inherited()
        self._lease.discard_inherited()

    def report(self) -> None:
        self._guard(self._sample_cpu)
        self._dispatch_if_due()

    def renew(self) -> None:
        if self._round_overdue():
            return self._release_overdue_round()

        try:
            if self._lease.request_if_due(hold=self._sampler.can_sample):
                self._lease_failures.recovered()
        except Exception as error:
            self._lease_failures.failed(error)

    def sample(self) -> None:
        self._lease.sample_if_due(self._round)

    def flush(self) -> None:
        self._dispatch(final=True)

    def _spawn(self, name: str, loop: Callable[[], None]) -> threading.Thread:
        thread = threading.Thread(target=loop, name=name, daemon=True)
        thread.start()
        return thread

    def _dispatch_loop(self) -> None:
        try:
            if self._configuration.prefork_web_handoff():
                self._pause(self.PREFORK_WAIT)
            self._cycle(self._dispatch_pass)
        finally:
            with self._mutex:
                self._ended = True
                owned = not self._handoff or self._abandoned
            if owned:
                self._guard(self._client.close)

    def _dispatch_pass(self) -> None:
        self._ensure_lease_loop()
        self.report()

    def _lease_loop(self) -> None:
        try:
            self._cycle(self._lease_pass)
        finally:
            self._guard(self._lease.close)

    def _lease_pass(self) -> None:
        self.renew()
        if self._lease.granted():
            self._ensure_sample_loop()

    def _sample_loop(self) -> None:
        try:
            while self._sampling():
                self._guard(self.sample)
                self._pause()
        finally:
            self._guard(lambda: plan.release_macros(self._logger()))

    def _cycle(self, step: Callable[[], None]) -> None:
        while self._live:
            self._guard(step)
            self._pause()

    def _pause(self, seconds: float | None = None) -> None:
        with self._mutex:
            if self._live:
                self._wake.wait(self.TICK if seconds is None else seconds)

    def _guard(self, step: Callable[[], object]) -> None:
        try:
            step()
        except BaseException as error:
            safe_log(self._logger(), "error", f"[HireFire] {format_error(error)}")

    def _ensure_lease_loop(self) -> None:
        if self._running(self._lease_thread) or self._running(self._predecessor):
            return
        if not self._enter_race():
            return

        self._lease_thread = self._spawn("hirefire-lease", self._lease_loop)

    def _ensure_sample_loop(self) -> None:
        if self._running(self.sample_thread):
            return

        self.sample_thread = self._spawn("hirefire-sample", self._sample_loop)

    @staticmethod
    def _running(thread: threading.Thread | None) -> bool:
        return thread is not None and thread.is_alive()

    def _sampling(self) -> bool:
        return self._live and self._lease.granted()

    def _round(self) -> None:
        self._round_started_at = time.monotonic()
        try:
            trace = self._sampler.round(self._lease.job_queues, self._sampling)
            with self._mutex:
                self._pending_sample_trace = trace
        finally:
            self._round_started_at = None

    def _round_overdue(self) -> bool:
        started = self._round_started_at
        return (
            started is not None and time.monotonic() - started > self.SAMPLE_ROUND_LIMIT
        )

    def _release_overdue_round(self) -> None:
        if not self._lease.granted():
            return

        self._lease.demote()
        safe_log(
            self._logger(),
            "warning",
            "[HireFire] A job queue sample round has run for more than "
            f"{self.SAMPLE_ROUND_LIMIT} seconds. The lease is released so that "
            "another process can sample.",
        )

    def _enter_race(self) -> bool:
        return self._configuration.job_queues.any() or plan.any_library_loaded()

    def _sample_cpu(self) -> None:
        for source in self._configuration.active_cpu_sources():
            self._guard(source.sample)

    def _dispatch_if_due(self) -> None:
        due = self._next_dispatch_at
        if due is not None and time.monotonic() < due:
            return

        self._dispatch()
        self._next_dispatch_at = time.monotonic() + self._dispatch_interval()

    def _dispatch_interval(self) -> int:
        doublings = min(self._dispatch_failures.count, self.BACKOFF_DOUBLINGS)
        return min(self._dispatch_frequency * 2**doublings, self.MAX_DISPATCH_FREQUENCY)

    def _dispatch(self, final: bool = False) -> None:
        if not (final or self._live):
            return

        data: Series | None = None
        try:
            data = self._buffer().flush()
            trace = self._pending_sample_trace
            entries, watermark = self._build_payload(data, trace)
            if not entries:
                return

            body = self._encode(entries)
            if len(body) > self.PAYLOAD_SIZE_LIMIT:
                return self._drop_oversized_payload(body, watermark, trace)

            self._submit(body, watermark, trace)
        except Exception as error:
            if data is not None and (self._live or self._handoff):
                self._repopulate_rqt(data)
            self._dispatch_failures.failed(error)

    def _encode(self, entries: list[dict[str, Any]]) -> str:
        body = json.dumps(entries, separators=(",", ":"))
        if len(body) <= self.PAYLOAD_SIZE_LIMIT or not payload.traced(entries):
            return body

        return json.dumps(payload.without_trace(entries), separators=(",", ":"))

    def _submit(self, body: str, watermark: int | None, trace: Trace | None) -> None:
        if verbose():
            safe_log(self._logger(), "info", f"[HireFire] Dispatching metrics: {body}")
        response = self._client.submit_samples(body)
        self._apply_dispatch_frequency(response)

        if response.too_large():
            self._drop_oversized_payload(body, watermark, trace, server=True)
        else:
            self._last_rqt_second = watermark
            self._clear_trace(trace)
            self._payload_drops.recovered()
        self._dispatch_failures.recovered()

    def _repopulate_rqt(self, data: Series) -> None:
        for name, strategies in data.items():
            buckets = strategies.get(RQT)
            if buckets:
                self._buffer().repopulate(name, RQT, buckets)

    def _apply_dispatch_frequency(self, response: Response) -> None:
        value = response.integer("HireFire-Dispatch-Frequency")
        if value is not None:
            self._dispatch_frequency = max(
                self.DEFAULT_DISPATCH_FREQUENCY,
                min(value, self.MAX_DISPATCH_FREQUENCY),
            )

    def _drop_oversized_payload(
        self,
        body: str,
        watermark: int | None,
        trace: Trace | None,
        server: bool = False,
    ) -> None:
        self._clear_trace(trace)
        self._last_rqt_second = watermark
        source = (
            "server rejected (413)"
            if server
            else f"exceeds the {self.PAYLOAD_SIZE_LIMIT}-byte limit"
        )
        self._payload_drops.record(
            f"Dropped metrics payload: {len(body)} bytes {source}. "
            "Resuming from the current second."
        )

    def _clear_trace(self, sent: Trace | None) -> None:
        with self._mutex:
            if self._pending_sample_trace is sent:
                self._pending_sample_trace = None

    def _build_payload(
        self, data: Series, trace: Trace | None
    ) -> tuple[list[dict[str, Any]], int | None]:
        configuration = self._configuration
        return payload.build(
            data,
            liveness=configuration.http_name() if configuration.rqt_enabled() else None,
            since=self._last_rqt_second,
            trace=trace if self._lease.trace() else None,
            omitted=self._log_omitted,
        )

    def _log_omitted(self, name: str, strategy: str) -> None:
        self._once.log(
            "error",
            "out_of_range",
            lambda: (
                f"[HireFire] Omitting {strategy} seconds of {name!r}: "
                "a value is out of range."
            ),
            key=(name, strategy),
        )

    def _buffer(self) -> "Buffer":
        return self._configuration.buffer

    def _logger(self) -> "logging.Logger":
        return self._configuration.logger
