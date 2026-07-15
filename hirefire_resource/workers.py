import math
from collections.abc import Iterator
from typing import TYPE_CHECKING

from hirefire_resource.log import safe_log
from hirefire_resource.worker import Worker

if TYPE_CHECKING:
    import logging

    from hirefire_resource.buffer import Buffer


class Workers:
    """Collection of job-metric Worker collectors declared on the configuration."""

    def __init__(self) -> None:
        self._workers: list[Worker] = []

    def append(self, worker: Worker) -> None:
        """Adds a worker collector to the collection."""
        self._workers.append(worker)

    def any(self) -> bool:
        """Whether any worker collectors are declared."""
        return len(self._workers) > 0

    def __iter__(self) -> Iterator[Worker]:
        return iter(self._workers)

    def __len__(self) -> int:
        return len(self._workers)

    def sample(self) -> None:
        """Samples every worker and buffers valid metric values.

        A value is valid when it is a non-boolean, non-negative, finite number.
        Invalid or raised sampler results are logged and skipped, not re-raised.
        """
        for worker in self._workers:
            try:
                value = worker.sample()

                if not self._valid_sample(value):
                    safe_log(
                        self._logger(),
                        "error",
                        f"[HireFire] The sampler for dyno {worker.name!r} returned "
                        f"{value!r}, expected a non-negative number. Sample dropped.",
                    )
                    continue

                self._buffer().sample_worker(worker.name, value)
            except Exception as error:
                safe_log(
                    self._logger(),
                    "error",
                    f"[HireFire] The sampler for dyno {worker.name!r} raised "
                    f"{type(error).__name__}: {error}",
                )

    def _valid_sample(self, value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )

    def _buffer(self) -> "Buffer":
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.buffer

    def _logger(self) -> "logging.Logger":
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.logger
