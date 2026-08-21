import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hirefire_resource.client import Client, RequestError
from hirefire_resource.log import safe_log


@dataclass(frozen=True)
class GrantBody:
    job_queues: list[dict[str, Any]]
    trace: bool = False


def empty_grant_body(*, trace: bool = False) -> GrantBody:
    return GrantBody(job_queues=[], trace=trace)


class Lease:
    TTL_BOUNDS = (5, 3600)
    SAMPLE_FREQUENCY_BOUNDS = (1, 3600)
    MAX_BODY_BYTES = 16_384
    MAX_JOB_QUEUES = 64
    MAX_NAME_BYTES = 128

    def __init__(self) -> None:
        self.process_id = str(uuid.uuid4())
        self._client = Client()
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
        self._epoch += 1
        self._granted = False
        self._trace = False
        self.job_queues = []
        self._expires_at = time.monotonic()
        self._next_sample_at = time.monotonic()

    def sample_if_due(self, sampler: Callable[[], None]) -> None:
        if self._owner_pid != os.getpid():
            self._reset_after_fork()
        if not (self._granted and time.monotonic() >= self._next_sample_at):
            return

        self._next_sample_at = time.monotonic() + self.sample_frequency
        sampler()

    def request_if_due(self, hold: Callable[[list[dict[str, Any]]], bool]) -> None:
        if self._owner_pid != os.getpid():
            self._reset_after_fork()
        if time.monotonic() < self._expires_at:
            return

        epoch = self._epoch
        self._expires_at = time.monotonic() + self._ttl

        try:
            response = self._client.request_lease(self.process_id)
        except Exception:
            if self._epoch != epoch:
                return
            self._granted = False
            self._trace = False
            self.job_queues = []
            raise

        if self._epoch != epoch:
            return

        if response.status == 401:
            self._granted = False
            self._trace = False
            self.job_queues = []
            return

        if not (200 <= response.status < 300):
            self._granted = False
            self._trace = False
            self.job_queues = []
            raise RequestError(f"Lease request failed with {response.status} status.")

        next_sample_frequency = self.sample_frequency
        next_sample_at = self._next_sample_at
        raw_frequency = response.headers.get("HireFire-Sample-Frequency")
        if raw_frequency is not None:
            previous_frequency = self.sample_frequency
            next_sample_frequency = self._bounded(
                raw_frequency, self.SAMPLE_FREQUENCY_BOUNDS
            )
            if next_sample_frequency < previous_frequency:
                sooner = time.monotonic() + next_sample_frequency
                if next_sample_at > sooner:
                    next_sample_at = sooner

        next_ttl = self._ttl
        next_expires_at = self._expires_at
        raw_ttl = response.headers.get("HireFire-Lease-TTL")
        if raw_ttl is not None:
            next_ttl = self._bounded(raw_ttl, self.TTL_BOUNDS)
            next_expires_at = time.monotonic() + next_ttl

        granted = response.headers.get("HireFire-Lease-Granted") == "true"
        grant_body = (
            self._parse_grant_body(response.body) if granted else empty_grant_body()
        )

        if self._epoch != epoch:
            return

        hold_ok = (not granted) or hold(grant_body.job_queues)

        if self._epoch != epoch:
            return

        self.sample_frequency = next_sample_frequency
        self._next_sample_at = next_sample_at
        self._ttl = next_ttl
        self._expires_at = next_expires_at

        if granted and not hold_ok:
            self._granted = False
            self._trace = False
            self.job_queues = []
            self.process_id = str(uuid.uuid4())
            from hirefire_resource.hirefire import HireFire

            safe_log(
                HireFire.configuration.logger,
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
        self._reset_after_fork()
        self._client._reinit_after_fork()

    def _reset_after_fork(self) -> None:
        self._epoch += 1
        self.process_id = str(uuid.uuid4())
        self._granted = False
        self._trace = False
        self.job_queues = []
        self._expires_at = time.monotonic()
        self._next_sample_at = time.monotonic()
        self._owner_pid = os.getpid()

    def _parse_grant_body(self, body: str | None) -> GrantBody:
        if body is None or body == "":
            return empty_grant_body()

        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        if len(body_bytes) > self.MAX_BODY_BYTES:
            self._log_error(
                f"[HireFire] Lease grant body exceeded {self.MAX_BODY_BYTES} bytes. "
                "Plan ignored."
            )
            return empty_grant_body()

        try:
            payload = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            self._log_error(
                "[HireFire] Lease grant body was not valid JSON. Plan ignored."
            )
            return empty_grant_body()

        if not isinstance(payload, dict):
            self._log_error(
                "[HireFire] Lease grant body was not a JSON object. Plan ignored."
            )
            return empty_grant_body()

        trace = payload.get("trace") is True
        entries = payload.get("job_queues")
        if not isinstance(entries, list):
            self._log_error(
                "[HireFire] Lease grant body job_queues was not an array. Plan ignored."
            )
            return empty_grant_body(trace=trace)

        accepted: list[dict[str, Any]] = []
        skipped = 0
        for entry in entries:
            if len(accepted) >= self.MAX_JOB_QUEUES:
                skipped += 1
                continue
            if not isinstance(entry, dict):
                skipped += 1
                continue

            name = self._wire_string(entry.get("name"))
            strategy = self._wire_string(entry.get("strategy"))
            adapter_present = "adapter" in entry
            adapter = self._wire_string(entry["adapter"]) if adapter_present else None
            if (
                not name
                or not strategy
                or len(name.encode("utf-8")) > self.MAX_NAME_BYTES
            ):
                skipped += 1
                continue

            normalized = dict(entry)
            normalized["name"] = name
            normalized["strategy"] = strategy
            if adapter_present:
                normalized["adapter"] = adapter
            accepted.append(normalized)

        if len(entries) > self.MAX_JOB_QUEUES:
            extra = f" ({skipped} invalid also skipped)" if skipped > 0 else ""
            self._log_error(
                f"[HireFire] Lease plan truncated to {self.MAX_JOB_QUEUES} job queue "
                f"entries{extra}."
            )
        elif skipped > 0:
            label = "entry" if skipped == 1 else "entries"
            self._log_error(
                f"[HireFire] Lease plan skipped {skipped} invalid job queue {label}."
            )

        return GrantBody(job_queues=accepted, trace=trace)

    def _log_error(self, message: str) -> None:
        from hirefire_resource.hirefire import HireFire

        safe_log(HireFire.configuration.logger, "error", message)

    @staticmethod
    def _wire_string(value: object | None) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _bounded(value: str, bounds: tuple[int, int]) -> int:
        low, high = bounds
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = low
        return max(low, min(parsed, high))
