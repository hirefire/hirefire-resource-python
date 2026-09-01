import math
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from hirefire_resource.log import safe_log
from hirefire_resource.source.job_queue import JobQueue

if TYPE_CHECKING:
    import logging

    from hirefire_resource.buffer import Buffer
    from hirefire_resource.configuration import Configuration


class JobQueues:
    def __init__(self, configuration: "Configuration | None" = None) -> None:
        self._configuration = configuration
        self._job_queues: list[JobQueue] = []

    def append(self, job_queue: JobQueue) -> None:
        self._job_queues.append(job_queue)

    def any(self) -> bool:
        return len(self._job_queues) > 0

    def __iter__(self) -> Iterator[JobQueue]:
        return iter(self._job_queues)

    def __len__(self) -> int:
        return len(self._job_queues)

    def find_by_name(self, name: str) -> JobQueue | None:
        needle = str(name).lower()
        for job_queue in self._job_queues:
            if job_queue.name.lower() == needle:
                return job_queue
        return None

    def sample_job_queue(
        self,
        job_queue: JobQueue | None,
        strategy: str,
        live: Callable[[], bool] | None = None,
        name: str | None = None,
    ) -> None:
        if job_queue is None:
            return

        report_name = (
            job_queue.name
            if name is None or not str(name).strip()
            else str(name).strip()
        )
        strategy = str(strategy)
        if strategy not in ("jql", "jqs"):
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Unknown job-queue strategy {strategy!r} for "
                f"{report_name!r}. Sample dropped.",
            )
            return

        try:
            value = job_queue.sample()
            if live is not None and not live():
                return

            if not self._valid_sample(value):
                safe_log(
                    self._logger(),
                    "error",
                    f"[HireFire] The sampler for {report_name!r} returned "
                    f"{self._format_sample_value(value)}, expected a non-negative "
                    "number. Sample dropped.",
                )
                return

            self._buffer().sample(report_name, strategy, value)
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] The sampler for {report_name!r} raised "
                f"{type(error).__name__}: {error}",
            )

    def _valid_sample(self, value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )

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
        return self._config().buffer

    def _logger(self) -> "logging.Logger":
        return self._config().logger

    def _config(self) -> "Configuration":
        if self._configuration is not None:
            return self._configuration
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration
