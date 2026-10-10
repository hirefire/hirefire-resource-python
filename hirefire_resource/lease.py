import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hirefire_resource.client import Client, Response
from hirefire_resource.errors import RequestError
from hirefire_resource.failure_log import FailureLog
from hirefire_resource.identity import MAX_NAME_BYTES, presence

if TYPE_CHECKING:
    from hirefire_resource.configuration import Configuration


class Lease:
    @dataclass(frozen=True)
    class GrantBody:
        job_queues: list[dict[str, Any]]
        trace: bool = False

    TTL_BOUNDS = (5, 3600)
    DROP_WAIT = 60
    SAMPLE_FREQUENCY_BOUNDS = (1, 3600)
    MAX_JOB_QUEUES = 256

    def __init__(self, configuration: "Configuration") -> None:
        self._configuration = configuration
        self.process_id = str(uuid.uuid4())
        self._client = Client(configuration)
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
        self._plan_failures = FailureLog("Lease plan", configuration)

    def granted(self) -> bool:
        return self._granted

    def trace(self) -> bool:
        return self._trace

    def demote(self) -> None:
        with self._mutex:
            self._apply_demote()

    def sample_if_due(self, sampler: Callable[[], None]) -> None:
        with self._mutex:
            if self._owner_pid != os.getpid():
                self._reset_after_fork()
            if not (self._granted and time.monotonic() >= self._next_sample_at):
                return
            self._next_sample_at = time.monotonic() + self.sample_frequency
        sampler()

    def request_if_due(self, hold: Callable[[list[dict[str, Any]]], bool]) -> bool:
        epoch = self._reserve()
        if epoch is None:
            return False
        response = self._fetch(epoch)
        if response is None:
            return False

        granted = response.headers.get("HireFire-Lease-Granted") == "true"
        grant = (
            self._parse_grant_body(response.body)
            if granted
            else self._empty_grant_body()
        )
        held = not granted or hold(grant.job_queues)

        with self._mutex:
            if self._epoch != epoch:
                return False

            self._apply_cadence(response)
            if held:
                self._apply_grant(granted, grant)
            else:
                self._drop_grant()
            return True

    def close(self) -> None:
        self._client.close()

    def discard_inherited(self) -> None:
        self._client.discard_inherited()

    def _reset_after_fork(self) -> None:
        self._apply_demote()
        self.process_id = str(uuid.uuid4())
        self._owner_pid = os.getpid()

    def _apply_demote(self) -> None:
        self._epoch += 1
        self._clear_grant()
        self._expires_at = time.monotonic()
        self._next_sample_at = time.monotonic()

    def _reserve(self) -> int | None:
        with self._mutex:
            if self._owner_pid != os.getpid():
                self._reset_after_fork()
            if time.monotonic() < self._expires_at:
                return None

            self._expires_at = time.monotonic() + self._ttl
            return self._epoch

    def _fetch(self, epoch: int) -> Response | None:
        try:
            response = self._client.request_lease(self.process_id)
        except Exception:
            if self._revoke(epoch):
                raise
            return None
        if response.ok():
            return response

        self._revoke(epoch)
        if response.unauthorized():
            return None
        raise RequestError(f"Lease request failed with {response.status} status.")

    def _revoke(self, epoch: int) -> bool:
        with self._mutex:
            current = self._epoch == epoch
            if current:
                self._clear_grant()
            return current

    def _apply_cadence(self, response: Response) -> None:
        frequency = response.integer("HireFire-Sample-Frequency")
        if frequency is not None:
            self.sample_frequency = self._clamp(frequency, self.SAMPLE_FREQUENCY_BOUNDS)
            self._next_sample_at = min(
                self._next_sample_at, time.monotonic() + self.sample_frequency
            )

        ttl = response.integer("HireFire-Lease-TTL")
        if ttl is not None:
            self._ttl = self._clamp(ttl, self.TTL_BOUNDS)
            self._expires_at = time.monotonic() + self._ttl

    def _apply_grant(self, granted: bool, grant: GrantBody) -> None:
        if not self._granted:
            self._next_sample_at = time.monotonic()
        self._granted = granted
        self._trace = grant.trace
        self.job_queues = grant.job_queues

    def _drop_grant(self) -> None:
        self._clear_grant()
        self.process_id = str(uuid.uuid4())
        self._expires_at = time.monotonic() + max(self.DROP_WAIT, 3 * self._ttl)
        self._configuration.once.log(
            "info",
            "lease_grant_dropped",
            lambda: (
                "[HireFire] Lease grant dropped: this process cannot sample the plan "
                "(no local job-queue samplers and no executable plan adapter)."
            ),
        )

    def _clear_grant(self) -> None:
        self._granted = False
        self._trace = False
        self.job_queues = []

    def _empty_grant_body(self, *, trace: bool = False) -> GrantBody:
        return self.GrantBody(job_queues=[], trace=trace)

    def _parse_grant_body(self, body: str) -> GrantBody:
        failures = self._plan_failures.count
        grant = self._read_grant_body(body)
        if self._plan_failures.count == failures:
            self._plan_failures.recovered()
        return grant

    def _read_grant_body(self, body: str) -> GrantBody:
        if not body:
            return self._empty_grant_body()

        try:
            payload = json.loads(body)
        except (TypeError, ValueError, RecursionError):
            return self._ignore_plan("was not valid JSON")
        if not isinstance(payload, dict):
            return self._ignore_plan("was not a JSON object")

        trace = payload.get("trace") is True
        entries = payload.get("job_queues")
        if not isinstance(entries, list):
            return self._ignore_plan("job_queues was not an array", trace=trace)

        return self.GrantBody(job_queues=self._plan_entries(entries), trace=trace)

    def _ignore_plan(self, reason: str, *, trace: bool = False) -> GrantBody:
        self._plan_failures.record(f"Lease grant body {reason}. Plan ignored.")
        return self._empty_grant_body(trace=trace)

    def _plan_entries(self, entries: list[Any]) -> list[dict[str, Any]]:
        valid = [
            entry for entry in map(self._normalize_entry, entries) if entry is not None
        ]
        invalid = len(entries) - len(valid)

        if len(valid) > self.MAX_JOB_QUEUES:
            extra = f" ({invalid} invalid also skipped)" if invalid else ""
            self._plan_failures.record(
                f"Lease plan truncated to {self.MAX_JOB_QUEUES} job queue "
                f"entries{extra}."
            )
        elif invalid:
            label = "entry" if invalid == 1 else "entries"
            self._plan_failures.record(
                f"Lease plan skipped {invalid} invalid job queue {label}."
            )

        return valid[: self.MAX_JOB_QUEUES]

    def _normalize_entry(self, entry: Any) -> dict[str, Any] | None:
        if not isinstance(entry, dict):
            return None

        name = presence(entry.get("name"))
        strategy = presence(entry.get("strategy"))
        if not name or not strategy or len(name.encode("utf-8")) > MAX_NAME_BYTES:
            return None

        normalized = {**entry, "name": name, "strategy": strategy}
        if "adapter" in entry:
            normalized["adapter"] = presence(entry["adapter"]) or ""
        return normalized

    @staticmethod
    def _clamp(value: int, bounds: tuple[int, int]) -> int:
        low, high = bounds
        return max(low, min(value, high))
