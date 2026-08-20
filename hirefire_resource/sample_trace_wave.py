"""One job-queue sample wave: start time, per-op timings, finish payload."""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

from hirefire_resource.log import safe_log

T = TypeVar("T")


class SampleTraceWave:
    """Monotonic start + ops list for a single ``sample_job_queues`` wave."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._ops: list[dict[str, Any]] = []
        self._payload: dict[str, Any] | None = None

    @classmethod
    def start(cls) -> SampleTraceWave:
        return cls()

    def measure(self, entry: Any, fn: Callable[[], T]) -> T:
        """Run ``fn``, record one op for ``entry``, return ``fn``'s result."""
        op_start = time.monotonic()
        result = fn()
        self.record(entry, self._elapsed_ms(op_start))
        return result

    def record(self, entry: Any, ms: float) -> SampleTraceWave:
        """Record one op with a pre-measured duration in milliseconds."""
        self._payload = None
        if not isinstance(entry, dict):
            entry = {}
        queues = entry.get("queues")
        options = entry.get("options")
        strategy = entry.get("strategy")
        if strategy is None:
            strategy_s = ""
        else:
            strategy_s = str(strategy)
        self._ops.append(
            {
                "adapter": entry.get("adapter"),
                "strategy": strategy_s,
                "queues": queues if isinstance(queues, list) else [],
                "options": options if isinstance(options, dict) else {},
                "ms": round(float(ms), 3),
            }
        )
        return self

    def finish(self) -> dict[str, Any]:
        """Wire payload: ``{"wave_ms": number, "ops": [...]}``.

        Ops is a copy so later ``record`` does not mutate a previous finish handle.
        """
        if self._payload is None:
            self._payload = {
                "wave_ms": self._elapsed_ms(self._start),
                "ops": [dict(op) for op in self._ops],
            }
        return self._payload

    def log_to(self, logger: Any) -> None:
        """Verbose sample timing lines (same format as the former dispatcher helper)."""
        payload = self.finish()
        safe_log(
            logger,
            "info",
            f"[HireFire] sample_job_queues wave_ms={payload['wave_ms']} "
            f"ops={len(payload['ops'])}",
        )
        for op in payload["ops"]:
            queues = ",".join(str(q) for q in op.get("queues") or [])
            safe_log(
                logger,
                "info",
                f"[HireFire] sample adapter={op.get('adapter')!r} "
                f"strategy={op.get('strategy')} queues={queues} ms={op.get('ms')}",
            )

    def _elapsed_ms(self, from_time: float) -> float:
        return round((time.monotonic() - from_time) * 1000.0, 3)
