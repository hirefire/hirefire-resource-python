from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from hirefire_resource import plan
from hirefire_resource.errors import SampleNotReadyError
from hirefire_resource.log import format_error, safe_log, verbose
from hirefire_resource.once import Once
from hirefire_resource.plan.entry import Entry
from hirefire_resource.probe import Probe
from hirefire_resource.sample import coerce_sample, format_sample_value, valid_sample
from hirefire_resource.strategy import WRK

if TYPE_CHECKING:
    from hirefire_resource.configuration import Configuration


class Sampler:
    def __init__(self, configuration: "Configuration") -> None:
        self._configuration = configuration
        self._once = Once(configuration)

    def can_sample(self, job_queues: list[dict[str, Any]]) -> bool:
        return self._configuration.job_queues.any() or any(
            Entry(raw).sampleable() for raw in job_queues
        )

    def round(
        self, job_queues: list[dict[str, Any]], live: Callable[[], bool]
    ) -> dict[str, Any]:
        probe = Probe.start()
        with plan.around_job_queue_sample(self._logger()):
            for raw in job_queues:
                if not live():
                    break
                probe.measure(raw, partial(self._sample, raw, live))
        if verbose():
            probe.log_to(self._logger())
        return probe.finish()

    def _sample(self, raw: dict[str, Any], live: Callable[[], bool]) -> None:
        try:
            entry = Entry(raw)
            if entry.problem is not None:
                self._once.log(
                    "error",
                    entry.problem,
                    lambda: f"[HireFire] {entry.problem_message()}",
                    key=entry.key(),
                )
            elif entry.local():
                self._sample_local(entry, live)
            else:
                self._sample_adapter(entry, live)
        except Exception as error:
            self._log_raised("Plan sampler", str(raw.get("name")), error)

    def _sample_local(self, entry: Entry, live: Callable[[], bool]) -> None:
        job_queues = self._configuration.job_queues
        job_queue = job_queues.find_by_name(entry.name)
        if job_queue is not None:
            self._record(
                "The sampler", entry.name, entry.strategy, live, job_queue.sample
            )
        elif job_queues.any():
            self._once.log(
                "warning",
                "no_local_sampler",
                lambda: (
                    f"[HireFire] No config.dyno sampler is named {entry.name!r}, so "
                    "this process samples nothing for it. Check the name, or select "
                    "an adapter for it in the HireFire UI."
                ),
                key=entry.name,
            )

    def _sample_adapter(self, entry: Entry, live: Callable[[], bool]) -> None:
        if self._configuration.job_queues.find_by_name(entry.name) is not None:
            self._once.log(
                "warning",
                "plan_override",
                lambda: (
                    f"[HireFire] A HireFire UI adapter is configured for "
                    f"{entry.name!r}, so config.dyno({entry.name!r}) with a local "
                    "sampler is ignored. You can remove that local configuration. "
                    "The UI adapter is used instead."
                ),
                key=entry.name,
            )
        if entry.truncated():
            self._once.log(
                "error",
                "queues_truncated",
                lambda: (
                    f"[HireFire] Plan queue list for {entry.name!r} truncated to "
                    f"{plan.MAX_QUEUES} names."
                ),
                key=entry.name,
            )

        self._record("Plan sampler", entry.name, entry.strategy, live, entry.call)
        if entry.samples_working():
            self._record("Plan working sampler", entry.name, WRK, live, entry.working)

    def _record(
        self,
        label: str,
        name: str,
        strategy: str,
        live: Callable[[], bool],
        sample: Callable[[], Any],
    ) -> None:
        try:
            value = sample()
            if not live():
                return

            if valid_sample(value):
                self._configuration.buffer.sample(name, strategy, coerce_sample(value))
            else:
                safe_log(
                    self._logger(),
                    "error",
                    f"[HireFire] {label} for {name!r} returned "
                    f"{format_sample_value(value)}, expected a non-negative number. "
                    "Sample dropped.",
                )
        except SampleNotReadyError:
            return
        except Exception as error:
            self._log_raised(label, name, error)

    def _log_raised(self, label: str, name: str, error: Exception) -> None:
        safe_log(
            self._logger(),
            "error",
            f"[HireFire] {label} for {name!r} raised {format_error(error)}",
        )

    def _logger(self) -> Any:
        return self._configuration.logger
