import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hirefire_resource.client import Client
from hirefire_resource.errors import RequestError
from hirefire_resource.log import safe_log


class Lease:
    @dataclass(frozen=True)
    class GrantBody:
        job_queues: list[dict[str, Any]]
        trace: bool = False

    TTL_BOUNDS = (5, 3600)
    DROP_WAIT = 60
    SAMPLE_FREQUENCY_BOUNDS = (1, 3600)
    MAX_JOB_QUEUES = 256
    MAX_NAME_BYTES = 128

    def __init__(self) -> None:
        self.process_id = str(uuid.uuid4())
        self._client = Client()
        self._mutex = threading.Lock()
        self._ttl = 15
        self._granted = False
        self._trace = False
        self._expires_at = time.monotonic()
        self._next_sample_at = time.monotonic()
        self.sample_frequency = 15
        self._owner_pid = os.getpid()
        self.job_queues: list[dict[str, Any]] = []
        self._epoch = 0

    def granted(self) -> bool:
        return self._granted

    def trace(self) -> bool:
        return self._trace

    def demote(self) -> None:
        with self._mutex:
            self._apply_demote()

    def sample_if_due(self, sampler: Callable[[], None]) -> None:
        due = False
        with self._mutex:
            if self._owner_pid != os.getpid():
                self._reset_after_fork()
            if not (self._granted and time.monotonic() >= self._next_sample_at):
                return
            self._next_sample_at = time.monotonic() + self.sample_frequency
            due = True
        if due:
            sampler()

    def request_if_due(self, hold: Callable[[list[dict[str, Any]]], bool]) -> None:
        epoch = None
        with self._mutex:
            if self._owner_pid != os.getpid():
                self._reset_after_fork()
            if time.monotonic() < self._expires_at:
                return
            epoch = self._epoch
            self._expires_at = time.monotonic() + self._ttl

        try:
            response = self._client.request_lease(self.process_id)
        except Exception:
            with self._mutex:
                if self._epoch != epoch:
                    return
                self._clear_grant()
            raise

        if not response.ok():
            with self._mutex:
                if self._epoch == epoch:
                    self._clear_grant()
            if response.unauthorized():
                return
            raise RequestError(f"Lease request failed with {response.status} status.")

        next_sample_frequency = self.sample_frequency
        next_sample_at = self._next_sample_at
        frequency = response.integer("HireFire-Sample-Frequency")
        if frequency is not None:
            next_sample_frequency = self._clamp(frequency, self.SAMPLE_FREQUENCY_BOUNDS)
            next_sample_at = min(
                next_sample_at, time.monotonic() + next_sample_frequency
            )

        next_ttl = self._ttl
        next_expires_at = self._expires_at
        ttl = response.integer("HireFire-Lease-TTL")
        if ttl is not None:
            next_ttl = self._clamp(ttl, self.TTL_BOUNDS)
            next_expires_at = time.monotonic() + next_ttl

        granted = response.headers.get("HireFire-Lease-Granted") == "true"
        grant_body = (
            self._parse_grant_body(response.body)
            if granted
            else self._empty_grant_body()
        )

        hold_ok = (not granted) or hold(grant_body.job_queues)

        with self._mutex:
            if self._epoch != epoch:
                return

            self.sample_frequency = next_sample_frequency
            self._next_sample_at = next_sample_at
            self._ttl = next_ttl
            self._expires_at = next_expires_at

            if granted and not hold_ok:
                self._clear_grant()
                self.process_id = str(uuid.uuid4())
                self._expires_at = time.monotonic() + max(self.DROP_WAIT, 3 * next_ttl)
                self._log(
                    "info",
                    "[HireFire] Lease grant dropped: this process cannot sample the plan "
                    "(no local job-queue samplers and no executable plan adapter).",
                )
            else:
                was_granted = self._granted
                self._granted = granted
                self._trace = granted and grant_body.trace
                self.job_queues = grant_body.job_queues
                if granted and not was_granted:
                    self._next_sample_at = time.monotonic()

    def close(self) -> None:
        self._client.close()

    def _reinit_after_fork(self) -> None:
        self._mutex = threading.Lock()
        self._reset_after_fork()
        self._client._reinit_after_fork()

    def _reset_after_fork(self) -> None:
        self._apply_demote()
        self.process_id = str(uuid.uuid4())
        self._owner_pid = os.getpid()

    def _apply_demote(self) -> None:
        self._epoch += 1
        self._clear_grant()
        self._expires_at = time.monotonic()
        self._next_sample_at = time.monotonic()

    def _clear_grant(self) -> None:
        self._granted = False
        self._trace = False
        self.job_queues = []

    def _empty_grant_body(self, *, trace: bool = False) -> GrantBody:
        return self.GrantBody(job_queues=[], trace=trace)

    def _parse_grant_body(self, body: str | None) -> GrantBody:
        if body is None or body == "":
            return self._empty_grant_body()

        try:
            payload = json.loads(body)
        except (TypeError, ValueError, RecursionError):
            self._log(
                "error",
                "[HireFire] Lease grant body was not valid JSON. Plan ignored.",
            )
            return self._empty_grant_body()

        if not isinstance(payload, dict):
            self._log(
                "error",
                "[HireFire] Lease grant body was not a JSON object. Plan ignored.",
            )
            return self._empty_grant_body()

        trace = payload.get("trace") is True
        entries = payload.get("job_queues")
        if not isinstance(entries, list):
            self._log(
                "error",
                "[HireFire] Lease grant body job_queues was not an array. Plan ignored.",
            )
            return self._empty_grant_body(trace=trace)

        return self.GrantBody(job_queues=self._plan_entries(entries), trace=trace)

    def _plan_entries(self, entries: list[Any]) -> list[dict[str, Any]]:
        valid = [
            entry for entry in map(self._normalize_entry, entries) if entry is not None
        ]
        invalid = len(entries) - len(valid)

        if len(valid) > self.MAX_JOB_QUEUES:
            extra = f" ({invalid} invalid also skipped)" if invalid else ""
            self._log(
                "error",
                f"[HireFire] Lease plan truncated to {self.MAX_JOB_QUEUES} job queue "
                f"entries{extra}.",
            )
        elif invalid:
            label = "entry" if invalid == 1 else "entries"
            self._log(
                "error",
                f"[HireFire] Lease plan skipped {invalid} invalid job queue {label}.",
            )

        return valid[: self.MAX_JOB_QUEUES]

    def _normalize_entry(self, entry: Any) -> dict[str, Any] | None:
        if not isinstance(entry, dict):
            return None

        name = self._wire_string(entry.get("name"))
        strategy = self._wire_string(entry.get("strategy"))
        if not name or not strategy or len(name.encode("utf-8")) > self.MAX_NAME_BYTES:
            return None

        normalized = {**entry, "name": name, "strategy": strategy}
        if "adapter" in entry:
            normalized["adapter"] = self._wire_string(entry["adapter"])
        return normalized

    def _log(self, level: str, message: str) -> None:
        from hirefire_resource.hirefire import HireFire

        safe_log(HireFire.configuration.logger, level, message)

    @staticmethod
    def _wire_string(value: object | None) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _clamp(value: int, bounds: tuple[int, int]) -> int:
        low, high = bounds
        return max(low, min(value, high))
