import json
import math
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from hirefire_resource.buffer import Buffer
from hirefire_resource.client import PAYLOAD_TOO_LARGE, Client, Response
from hirefire_resource.lease import Lease
from hirefire_resource.log import safe_log
from hirefire_resource.sample_trace_wave import SampleTraceWave

if TYPE_CHECKING:
    import logging


def _plan() -> Any:
    from hirefire_resource import plan

    return plan


class Dispatcher:
    """Periodic reporter that samples job queues and CPU and flushes buffered metrics to the API."""

    RQT_BACKFILL_LIMIT = 60
    PAYLOAD_SIZE_LIMIT = 32_768
    WARN_MAP_LIMIT = 128
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
        self._unloaded_adapter_warned: dict[str, bool] = {}
        self._plan_override_warned: dict[str, bool] = {}
        self._unknown_adapter_warned: dict[str, bool] = {}
        self._unsupported_strategy_warned: dict[str, bool] = {}
        self._unknown_strategy_warned: dict[str, bool] = {}
        self._empty_queues_warned: dict[str, bool] = {}

    def start(self) -> bool:
        """Starts the dispatcher loops.

        Returns:
            ``True`` when started. ``False`` if already running in this process,
            or if starting the loops failed (the failure is logged).
        """
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
                    _plan().reinit_macros_after_fork()
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
        """Ensures the job-queue loop is running when lease race entry becomes true after a late configure."""
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
        """Stops the dispatcher loops and closes transport resources.

        Wakes interval waiters, then joins local loop threads for up to
        ``JOIN_TIMEOUT`` seconds each. A hung sampler is abandoned rather than
        killed. A later :meth:`start` increments the loop generation so an
        abandoned thread cannot resume work. Concurrent :meth:`start` is rejected
        until close finishes.

        Args:
            flush: When ``True`` (default), best-effort final metric flush before close.
                Prefork parents pass ``False`` so the master does not claim empty web
                liveness after workers take over.

        Returns:
            ``True`` once the dispatcher has stopped, ``False`` when it was not
            running.
        """
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
        """Whether the dispatcher is currently running in this process."""
        with self._mutex:
            return self._healthy_running_locked()

    def abandon_inherited_state(self) -> None:
        """Child-side cleanup after a fork that does not restart reporting.

        Clears inherited loop flags, buffer, and lease so a short-lived child cannot
        flush the parent's samples.
        """
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
            _plan().reinit_macros_after_fork()
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
                    f"[HireFire] {type(error).__name__}: {error}",
                )
            self._wait_loop_interval(generation)

    def _wait_loop_interval(self, generation: int) -> None:
        # Interruptible 1s gap between ticks. Do not replace with time.sleep:
        # stop would wait out the remainder, and freeze_time can hang sleep.
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

        self._guard(lambda: self._lease.request_if_due(hold=self._hold_lease))
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
        self._unloaded_adapter_warned = {}
        self._plan_override_warned = {}
        self._unknown_adapter_warned = {}
        self._unsupported_strategy_warned = {}
        self._unknown_strategy_warned = {}
        self._empty_queues_warned = {}

    def _enter_race(self) -> bool:
        return (
            self._configuration().job_queues.any()
            or _plan().any_allowlisted_job_queue_library_loaded()
        )

    def _hold_lease(self, plan_job_queues: list[dict[str, Any]]) -> bool:
        if self._configuration().job_queues.any():
            return True

        for entry in plan_job_queues:
            if self._adapter_present(entry) and _plan().sampleable_entry(entry):
                return True
        return False

    def _sample_job_queues(self, live: Callable[[], bool] | None = None) -> None:
        wave = SampleTraceWave.start()
        with _plan().around_job_queue_sample():
            local_job_queues = self._configuration().job_queues
            for entry in self._lease.job_queues:
                if live is not None and not live():
                    break

                def _sample(entry: dict[str, Any] = entry) -> None:
                    if self._adapter_present(entry):
                        self._sample_plan_adapter(entry, local_job_queues, live=live)
                    else:
                        self._sample_strategy_only(entry, local_job_queues, live=live)

                wave.measure(entry, _sample)
        payload = wave.finish()
        if self._verbose():
            wave.log_to(self._logger())
        if self._lease.trace():
            self._pending_sample_trace = payload

    def _verbose(self) -> bool:
        value = os.environ.get("HIREFIRE_VERBOSE", "")
        if not value:
            return False
        return value.lower() not in ("0", "false", "no")

    def _sample_plan_adapter(
        self,
        entry: dict[str, Any],
        local_job_queues: Any,
        live: Callable[[], bool] | None = None,
    ) -> None:
        if live is not None and not live():
            return

        name = str(entry.get("name", ""))
        adapter = entry.get("adapter")
        strategy = entry.get("strategy")
        plan = _plan()

        if plan.executable(adapter):
            if not plan.supports_strategy(adapter, strategy):
                self._warn_unsupported_strategy_once(name, adapter, strategy)
                return
            if plan.queues_required(adapter) and not plan.named_plan_queues(
                entry.get("queues")
            ):
                self._warn_empty_queues_once(name, adapter)
                return
            if local_job_queues.find_by_name(name) is not None:
                self._warn_plan_override_once(name)
            if live is not None and not live():
                return
            plan.execute(entry, live)
        elif plan.known_adapter(adapter):
            self._warn_unloaded_adapter_once(name, adapter)
        else:
            self._warn_unknown_adapter_once(name, adapter)

    def _sample_strategy_only(
        self,
        entry: dict[str, Any],
        local_job_queues: Any,
        live: Callable[[], bool] | None = None,
    ) -> None:
        if live is not None and not live():
            return

        name = str(entry.get("name", ""))
        strategy = str(entry.get("strategy", ""))

        if not _plan().known_strategy(strategy):
            self._warn_unknown_strategy_once(name, strategy)
            return

        job_queue = local_job_queues.find_by_name(name)
        if job_queue is not None:
            local_job_queues.sample_job_queue(
                job_queue, strategy, live=live, name=name.strip()
            )

    def _remember_warn(self, store: dict[str, bool], key: str) -> bool:
        if key in store:
            return True
        while len(store) >= self.WARN_MAP_LIMIT:
            store.pop(next(iter(store)))
        store[key] = True
        return False

    def _warn_unloaded_adapter_once(self, name: str, adapter: object) -> None:
        if self._remember_warn(self._unloaded_adapter_warned, name):
            return
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] Plan adapter {adapter!r} for {name!r} "
            "is not loaded in this process. Entry skipped.",
        )

    def _warn_plan_override_once(self, name: str) -> None:
        if self._remember_warn(self._plan_override_warned, name):
            return
        safe_log(
            self._logger(),
            "warning",
            f"[HireFire] A HireFire UI adapter is configured for {name!r}, so "
            f"config.dyno({name!r}) with a local sampler is ignored. You can remove "
            "that local configuration. The UI adapter is used instead.",
        )

    def _warn_unknown_adapter_once(self, name: str, adapter: object) -> None:
        if self._remember_warn(self._unknown_adapter_warned, name):
            return
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] Unknown plan adapter {adapter!r} for {name!r}. Entry skipped.",
        )

    def _warn_unsupported_strategy_once(
        self, name: str, adapter: object, strategy: object
    ) -> None:
        key = f"{name}\0{adapter}\0{strategy}"
        if self._remember_warn(self._unsupported_strategy_warned, key):
            return
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] Plan adapter {adapter!r} does not support "
            f"strategy {strategy!r} for {name!r}. Entry skipped.",
        )

    def _warn_unknown_strategy_once(self, name: str, strategy: object) -> None:
        key = f"{name}\0{strategy}"
        if self._remember_warn(self._unknown_strategy_warned, key):
            return
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] Unknown plan strategy {strategy!r} for "
            f"{name!r}. Entry skipped.",
        )

    def _warn_empty_queues_once(self, name: str, adapter: object) -> None:
        key = f"{name}\0{adapter}"
        if self._remember_warn(self._empty_queues_warned, key):
            return
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] Plan adapter {adapter!r} for {name!r} "
            "requires named queues. Entry skipped.",
        )

    @staticmethod
    def _adapter_present(entry: dict[str, Any]) -> bool:
        adapter = entry.get("adapter")
        return not (adapter is None or adapter == "")

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
                f"[HireFire] {type(error).__name__}: {error}",
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

            if self._verbose():
                safe_log(
                    self._logger(),
                    "info",
                    f"[HireFire] Dispatching metrics: {body}",
                )
            response = self._client.submit_samples(body)

            if generation is not None and not self._loop_active(generation):
                return

            if response == PAYLOAD_TOO_LARGE:
                return self._drop_oversized_payload(body, watermark, server=True)

            self._apply_dispatch_frequency(
                response if isinstance(response, Response) else None
            )
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
                f"[HireFire] Dispatch error: {type(error).__name__}: {error}",
            )

    def _handoff_to_final_flush(self) -> bool:
        with self._mutex:
            return self._stopping and self._stopping_flush

    def _repopulate_rqt(self, data: dict[str, dict[str, dict[int, Any]]]) -> None:
        for name, strategies in data.items():
            series = strategies.get("rqt")
            if series:
                self._buffer().repopulate(name, "rqt", series)

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
                if strategy_key == "rqt" and name == http_name:
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

        rqt_buckets = data.get(http_name, {}).get("rqt") or {}
        configuration = self._configuration()

        if configuration.rqt_enabled() and configuration.rqt_liveness():
            payload_rqt = self._backfill_rqt_seconds(rqt_buckets)
            self._merge_metrics(entries_by_name, http_name, "rqt", payload_rqt)
            return max(payload_rqt.keys()) if payload_rqt else None
        if rqt_buckets:
            self._merge_metrics(entries_by_name, http_name, "rqt", rqt_buckets)
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
            if strategy == "rqt":
                sum_v, count = self._rqt_parts(bucket)
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
        if strategy == "rqt":
            sum_v, count = self._rqt_parts(bucket)
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

    @staticmethod
    def _rqt_parts(bucket: Any) -> tuple[float, int]:
        if isinstance(bucket, dict):
            return float(bucket.get("sum", 0.0)), int(bucket.get("count", 0))
        return 0.0, 0

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
            sum_v, count = self._rqt_parts(bucket)
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
