import math
from collections.abc import Iterator
from typing import TYPE_CHECKING

from hirefire_resource.log import safe_log
from hirefire_resource.worker import Worker

if TYPE_CHECKING:
    import logging

    from hirefire_resource.buffer import Buffer


class Workers:
    """Collection of local job-queue sources declared on the configuration."""

    def __init__(self) -> None:
        self._workers: list[Worker] = []

    def append(self, worker: Worker) -> None:
        self._workers.append(worker)

    def any(self) -> bool:
        return len(self._workers) > 0

    def __iter__(self) -> Iterator[Worker]:
        return iter(self._workers)

    def __len__(self) -> int:
        return len(self._workers)

    def find_by_name(self, name: str) -> Worker | None:
        needle = str(name).lower()
        for worker in self._workers:
            if worker.name.lower() == needle:
                return worker
        return None

    def sample_job_queue(self, job_queue: Worker | None, strategy: str) -> None:
        if job_queue is None:
            return

        strategy = str(strategy)
        if strategy not in ("jql", "jqs"):
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Unknown job-queue strategy {strategy!r} for "
                f"{job_queue.name!r}. Sample dropped.",
            )
            return

        try:
            value = job_queue.sample()

            if not self._valid_sample(value):
                safe_log(
                    self._logger(),
                    "error",
                    f"[HireFire] The sampler for {job_queue.name!r} returned "
                    f"{self._format_sample_value(value)}, expected a non-negative "
                    "number. Sample dropped.",
                )
                return

            self._buffer().sample(job_queue.name, strategy, self._coerce_sample(value))
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] The sampler for {job_queue.name!r} raised "
                f"{type(error).__name__}: {error}",
            )

    def _valid_sample(self, value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )

    def _coerce_sample(self, value: int | float) -> int | float:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return value
        return float(value)

    def _format_sample_value(self, value: object) -> str:
        try:
            text = type(value).__name__
            preview = str(value)
            encoded = preview.encode("utf-8")
            if len(encoded) > 64:
                preview = encoded[:64].decode("utf-8", "replace") + "…"
            return f"{text}({preview!r})"
        except Exception:
            return type(value).__name__

    def _buffer(self) -> "Buffer":
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.buffer

    def _logger(self) -> "logging.Logger":
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.logger
