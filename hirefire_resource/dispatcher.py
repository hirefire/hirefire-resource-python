import json
import math
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from hirefire_resource.buffer import Buffer
from hirefire_resource.client import Client, Response
from hirefire_resource.lease import Lease
from hirefire_resource.log import format_error, safe_log, verbose
from hirefire_resource.sampler import Sampler
from hirefire_resource.strategy import RQT, rqt

if TYPE_CHECKING:
    import logging


def _plan() -> Any:
    from hirefire_resource import plan

    return plan


class Dispatcher:
    RQT_BACKFILL_LIMIT = 60
    PAYLOAD_SIZE_LIMIT = 131_072
    SAMPLE_COUNT_LIMIT = Buffer.SAMPLE_COUNT_LIMIT
    METRIC_VALUE_LIMIT = 1e15
    DEFAULT_DISPATCH_FREQUENCY = 1
    MAX_DISPATCH_FREQUENCY = 30
    JOIN_TIMEOUT = 5

    def __init__(self) -> None:
        self._client = Client()
        self._lease = Lease()
        self._mutex = threading.Lock()
        self._loop_wait = threading.Condition(self._mutex)
        self._join_timeout = self.JOIN_TIMEOUT
        self._running = False
        self._stopping = False
        self._stopping_flush = False
        self._pid: int | None = None
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._job_queue_thread: threading.Thread | None = None
        self._last_rqt_second: int | None = None
        self._dispatch_frequency = self.DEFAULT_DISPATCH_FREQUENCY
        self._next_dispatch_at: float | None = None
        self._pending_sample_trace: dict[str, Any] | None = None
        self._sampler = Sampler(self._configuration())

    def start(self) -> bool:
        if self._healthy_running():
            return False

        retired_jq: threading.Thread | None = None

        try:
            with self._mutex:
                if self._stopping:
                    return False
                if self._healthy_running_locked():
                    return False

                if (
                    self._running
                    and self._pid == os.getpid()
                    and (self._thread is None or not self._thread.is_alive())
                ):
                    self._running = False
                    self._thread = None
                    if (
                        self._job_queue_thread is not None
                        and self._job_queue_thread.is_alive()
                    ):
                        retired_jq = self._job_queue_thread
                        self._job_queue_thread = None

                after_fork = self._pid is not None and self._pid != os.getpid()
                if after_fork:
                    self._buffer().reinit_after_fork()
                    _plan().reinit_macros_after_fork(self._logger())
                    self._reset_dispatch_state_after_fork()
                else:
                    self._reset_dispatch_state_for_restart()
                    self._lease.demote()

                self._generation += 1
                generation = self._generation
                self._loop_wait.notify_all()
                self._thread = threading.Thread(
                    target=self._loop_until_stopped,
                    args=(generation, self._tick),
                    daemon=True,
                )
                self._thread.start()
                if self._enter_race():
                    self._job_queue_thread = threading.Thread(
                        target=self._loop_until_stopped,
                        args=(generation, self._job_queue_tick),
                        daemon=True,
                    )
                    self._job_queue_thread.start()
                else:
                    self._job_queue_thread = None
                self._running = True
                self._pid = os.getpid()
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Could not start dispatcher: {error}",
            )
            return False

        if retired_jq is not None:
            self._join_loop_thread(retired_jq)

        safe_log(self._logger(), "info", "[HireFire] Starting dispatcher.")
        return True

    def ensure_job_queue_loop(self) -> None:
        jq_thread = self._job_queue_thread
        if (
            jq_thread is not None
            and jq_thread.is_alive()
            and self._running
            and self._pid == os.getpid()
            and not self._stopping
        ):
            return
        if not self._enter_race():
            return

        try:
            with self._mutex:
                if self._stopping:
                    return
                if not (self._running and self._pid == os.getpid()):
                    return
                jq_thread = self._job_queue_thread
                if jq_thread is not None and jq_thread.is_alive():
                    return
                if not self._enter_race():
                    return

                generation = self._generation
                self._job_queue_thread = threading.Thread(
                    target=self._loop_until_stopped,
                    args=(generation, self._job_queue_tick),
                    daemon=True,
                )
                self._job_queue_thread.start()
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Could not start job-queue loop: {error}",
            )

    def stop(self, flush: bool = True) -> bool:
        threads: list[threading.Thread] = []

        with self._mutex:
            if not self._running:
                return False
            if self._stopping:
                return False

            self._stopping = True
            self._stopping_flush = flush
            self._running = False
            self._loop_wait.notify_all()
            if self._pid == os.getpid():
                threads = [
                    thread
                    for thread in (self._thread, self._job_queue_thread)
                    if thread is not None
                ]
            self._thread = None
            self._job_queue_thread = None

        try:
            for thread in threads:
                self._join_loop_thread(thread)

            if flush:
                self._dispatch(generation=None)
            else:
                self._buffer().discard_inherited()

            safe_log(self._logger(), "info", "[HireFire] Dispatcher stopped.")
            return True
        finally:
            try:
                self._client.close()
            except Exception as error:
                safe_log(
                    self._logger(),
                    "error",
                    f"[HireFire] Client close error: {error}",
                )
            try:
                self._lease.demote()
                self._lease.close()
            except Exception as error:
                safe_log(
                    self._logger(),
                    "error",
                    f"[HireFire] Lease close error: {error}",
                )
            with self._mutex:
                self._stopping = False
                self._stopping_flush = False

    def running(self) -> bool:
        with self._mutex:
            return self._healthy_running_locked()

    def abandon_inherited_state(self) -> None:
        try:
            with self._mutex:
                self._running = False
                self._stopping = False
                self._stopping_flush = False
                self._thread = None
                self._job_queue_thread = None
                self._pid = None
                self._generation += 1
                self._loop_wait.notify_all()
            self._buffer().reinit_after_fork()
            _plan().reinit_macros_after_fork(self._logger())
            self._configuration().reset_after_fork()
            self._lease.demote()
            self._client.close()
            self._lease.close()
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Could not abandon inherited dispatcher state: {error}",
            )

    def _reinit_locks_after_fork(self) -> None:
        self._mutex = threading.Lock()
        self._loop_wait = threading.Condition(self._mutex)
        self._client._reinit_after_fork()
        self._lease._reinit_after_fork()

    def _healthy_running(self) -> bool:
        thread = self._thread
        return (
            self._running
            and not self._stopping
            and self._pid == os.getpid()
            and thread is not None
            and thread.is_alive()
        )

    def _healthy_running_locked(self) -> bool:
        return self._healthy_running()

    def _loop_active(self, generation: int) -> bool:
        with self._mutex:
            return self._loop_active_locked(generation)

    def _loop_active_locked(self, generation: int) -> bool:
        return (
            self._running
            and not self._stopping
            and self._pid == os.getpid()
            and self._generation == generation
        )

    def _loop_until_stopped(
        self, generation: int, tick: Callable[[int | None], None]
    ) -> None:
        while self._loop_active(generation):
            try:
                tick(generation)
            except Exception as error:
                safe_log(
                    self._logger(),
                    "error",
                    f"[HireFire] {format_error(error)}",
                )
            self._wait_loop_interval(generation)

    def _wait_loop_interval(self, generation: int) -> None:
        with self._mutex:
            if self._loop_active_locked(generation):
                self._loop_wait.wait(timeout=1)

    def _join_loop_thread(self, thread: threading.Thread) -> None:
        thread.join(self._join_timeout)
        if thread.is_alive():
            safe_log(
                self._logger(),
                "warning",
                f"[HireFire] Dispatcher loop did not stop within {self._join_timeout}s. "
                "Abandoning thread.",
            )

    def _tick(self, generation: int | None = None) -> None:
        if generation is not None and not self._loop_active(generation):
            return

        sources: list[Any] = []
        self._guard(lambda: sources.extend(self._configuration().active_cpu_sources()))
        for source in sources:
            self._guard(source.sample)
        self._dispatch_if_due(generation)

    def _loop_live_proc(self, generation: int | None) -> Callable[[], bool]:
        if generation is not None:
            return lambda: self._loop_active(generation)
        return lambda: True

    def _job_queue_tick(self, generation: int | None = None) -> None:
        live = self._loop_live_proc(generation)
        if not live():
            return

        self._guard(lambda: self._lease.request_if_due(hold=self._sampler.can_sample))
        if not live():
            return
        self._guard(
            lambda: self._lease.sample_if_due(
                lambda: self._sample_job_queues(live=live)
            )
        )

    def _reset_dispatch_state_after_fork(self) -> None:
        self._reset_dispatch_state_for_restart()
        self._configuration().reset_after_fork()

    def _reset_dispatch_state_for_restart(self) -> None:
        self._next_dispatch_at = None
        self._last_rqt_second = None
        self._dispatch_frequency = self.DEFAULT_DISPATCH_FREQUENCY
        self._pending_sample_trace = None
        self._sampler = Sampler(self._configuration())

    def _enter_race(self) -> bool:
        return self._configuration().job_queues.any() or _plan().any_library_loaded()

    def _sample_job_queues(self, live: Callable[[], bool] | None = None) -> None:
        payload = self._sampler.round(self._lease.job_queues, live or (lambda: True))
        if self._lease.trace():
            self._pending_sample_trace = payload

    def _dispatch_if_due(self, generation: int | None = None) -> None:
        if (
            self._next_dispatch_at is not None
            and time.monotonic() < self._next_dispatch_at
        ):
            return
        if generation is not None and not self._loop_active(generation):
            return

        self._dispatch(generation)
        if generation is None or self._loop_active(generation):
            self._next_dispatch_at = time.monotonic() + self._dispatch_frequency

    def _guard(self, func: Callable[[], object]) -> None:
        try:
            func()
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] {format_error(error)}",
            )

    def _dispatch(self, generation: int | None = None) -> None:
        data: dict[str, dict[str, dict[int, Any]]] | None = None
        try:
            if generation is not None and not self._loop_active(generation):
                return

            data = self._buffer().flush()
            payload, watermark = self._build_payload(data)
            if not payload:
                return

            body = json.dumps(payload, separators=(",", ":"))
            if len(
                body.encode("utf-8")
            ) > self.PAYLOAD_SIZE_LIMIT and self._payload_has_sample_trace(payload):
                payload = self._strip_sample_trace(payload)
                body = json.dumps(payload, separators=(",", ":"))
            if len(body.encode("utf-8")) > self.PAYLOAD_SIZE_LIMIT:
                if not (
                    generation is None
                    or self._loop_active(generation)
                    or self._handoff_to_final_flush()
                ):
                    return
                return self._drop_oversized_payload(body, watermark)

            if generation is not None and not self._loop_active(generation):
                if self._handoff_to_final_flush():
                    self._repopulate_rqt(data)
                return

            if verbose():
                safe_log(
                    self._logger(),
                    "info",
                    f"[HireFire] Dispatching metrics: {body}",
                )
            response = self._client.submit_samples(body)

            if generation is not None and not self._loop_active(generation):
                return

            self._apply_dispatch_frequency(response)
            if response.too_large():
                return self._drop_oversized_payload(body, watermark, server=True)

            if watermark is not None:
                self._last_rqt_second = watermark
            self._pending_sample_trace = None
        except Exception as error:
            if data is not None and (
                generation is None
                or self._loop_active(generation)
                or self._handoff_to_final_flush()
            ):
                self._repopulate_rqt(data)
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Dispatch error: {format_error(error)}",
            )

    def _handoff_to_final_flush(self) -> bool:
        with self._mutex:
            return self._stopping and self._stopping_flush

    def _repopulate_rqt(self, data: dict[str, dict[str, dict[int, Any]]]) -> None:
        for name, strategies in data.items():
            series = strategies.get(RQT)
            if series:
                self._buffer().repopulate(name, RQT, series)

    def _apply_dispatch_frequency(self, response: Response) -> None:
        value = response.integer("HireFire-Dispatch-Frequency")
        if value is None:
            return

        self._dispatch_frequency = max(
            self.DEFAULT_DISPATCH_FREQUENCY,
            min(value, self.MAX_DISPATCH_FREQUENCY),
        )

    def _drop_oversized_payload(
        self, body: str, watermark: int | None, server: bool = False
    ) -> None:
        self._pending_sample_trace = None
        if watermark is not None:
            self._last_rqt_second = watermark
        source = (
            "server rejected (413)"
            if server
            else f"exceeds the {self.PAYLOAD_SIZE_LIMIT}-byte limit"
        )
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] Dropped metrics payload: {len(body.encode('utf-8'))} bytes "
            f"{source}. Resuming from the current second.",
        )

    def _build_payload(
        self, data: dict[str, dict[str, dict[int, Any]]]
    ) -> tuple[list[Any], int | None]:
        entries_by_name: dict[str, dict[str, dict[int, Any]]] = {}
        http_name = self._configuration().http_name()
        watermark = self._append_http_rqt(entries_by_name, data, http_name)

        for name, strategies in data.items():
            for strategy, series in strategies.items():
                strategy_key = str(strategy)
                if not series:
                    continue
                if rqt(strategy_key) and name == http_name:
                    continue
                self._merge_metrics(entries_by_name, name, strategy_key, series)

        entries: list[Any] = []
        for name, metrics in entries_by_name.items():
            encoded: dict[str, dict[str, Any]] = {}
            for strategy, series in metrics.items():
                strategy_key = str(strategy)
                leaf_series: dict[str, Any] = {}
                for second, bucket in series.items():
                    leaf = self._encode_leaf(strategy_key, bucket)
                    if leaf is None:
                        continue
                    leaf_series[str(second)] = leaf
                if leaf_series:
                    encoded[strategy_key] = leaf_series
            if encoded:
                entries.append({"name": name, "metrics": encoded})

        self._attach_sample_trace(entries)
        return entries, watermark

    def _payload_has_sample_trace(self, payload: list[Any]) -> bool:
        return bool(
            payload and isinstance(payload[0], dict) and "sample_trace" in payload[0]
        )

    def _strip_sample_trace(self, payload: list[Any]) -> list[Any]:
        self._pending_sample_trace = None
        stripped: list[Any] = []
        for entry in payload:
            if isinstance(entry, dict) and "sample_trace" in entry:
                copy = dict(entry)
                copy.pop("sample_trace", None)
                stripped.append(copy)
            else:
                stripped.append(entry)
        return stripped

    def _attach_sample_trace(self, entries: list[Any]) -> None:
        if self._pending_sample_trace is None or not entries or not self._lease.trace():
            return
        entries[0]["sample_trace"] = self._pending_sample_trace

    def _append_http_rqt(
        self,
        entries_by_name: dict[str, dict[str, dict[int, Any]]],
        data: dict[str, dict[str, dict[int, Any]]],
        http_name: str | None,
    ) -> int | None:
        if http_name is None:
            return None

        rqt_buckets = data.get(http_name, {}).get(RQT) or {}
        configuration = self._configuration()

        if configuration.rqt_enabled() and configuration.rqt_liveness():
            payload_rqt = self._backfill_rqt_seconds(rqt_buckets)
            self._merge_metrics(entries_by_name, http_name, RQT, payload_rqt)
            return max(payload_rqt.keys()) if payload_rqt else None
        if rqt_buckets:
            self._merge_metrics(entries_by_name, http_name, RQT, rqt_buckets)
            return None
        return None

    def _merge_metrics(
        self,
        entries_by_name: dict[str, dict[str, dict[int, Any]]],
        name: str,
        strategy: str,
        series_buckets: dict[int, Any],
    ) -> None:
        dest_name = entries_by_name.setdefault(name, {})
        dest = dest_name.setdefault(strategy, {})
        for second, bucket in series_buckets.items():
            if rqt(strategy):
                sum_v, count = Buffer.rqt_parts(bucket)
                existing = dest.get(second)
                if existing is None:
                    dest[second] = {"sum": sum_v, "count": count}
                else:
                    dest[second] = {
                        "sum": existing["sum"] + sum_v,
                        "count": existing["count"] + count,
                    }
            else:
                dest[second] = bucket

    def _encode_leaf(self, strategy: str, bucket: Any) -> Any | None:
        if rqt(strategy):
            sum_v, count = Buffer.rqt_parts(bucket)
            if count == 0:
                return []
            mean = sum_v / count
            if not math.isfinite(mean) or mean < 0 or mean > self.METRIC_VALUE_LIMIT:
                safe_log(
                    self._logger(),
                    "error",
                    "[HireFire] Omitting rqt second: non-finite or out-of-range mean.",
                )
                return None
            n = count if count <= self.SAMPLE_COUNT_LIMIT else self.SAMPLE_COUNT_LIMIT
            return [mean, n]
        if not isinstance(bucket, (int, float)) or isinstance(bucket, bool):
            return None
        if not math.isfinite(bucket) or bucket < 0 or bucket > self.METRIC_VALUE_LIMIT:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Omitting {strategy} second: non-finite or out-of-range value.",
            )
            return None
        return bucket

    def _backfill_rqt_seconds(self, buckets: dict[int, Any]) -> dict[int, Any]:
        now = int(time.time())
        from_second = (
            self._last_rqt_second + 1 if self._last_rqt_second is not None else now
        )
        if from_second < now - self.RQT_BACKFILL_LIMIT:
            from_second = now - self.RQT_BACKFILL_LIMIT
        if from_second > now:
            from_second = now

        payload: dict[int, Any] = {}
        for second, bucket in buckets.items():
            sum_v, count = Buffer.rqt_parts(bucket)
            payload[second] = {"sum": sum_v, "count": count}
        for second in range(from_second, now + 1):
            payload.setdefault(second, {"sum": 0.0, "count": 0})
        return payload

    def _buffer(self) -> Buffer:
        return self._configuration().buffer

    def _configuration(self) -> Any:
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration

    def _logger(self) -> "logging.Logger":
        return self._configuration().logger
