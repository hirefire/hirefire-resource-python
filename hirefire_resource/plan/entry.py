from typing import Any

from hirefire_resource import plan
from hirefire_resource.log import format_error
from hirefire_resource.strategy import JQL, JQS


def _text(value: object) -> str:
    return "" if value is None else str(value)


class Entry:
    METHODS = {JQL: "job_queue_latency", JQS: "job_queue_size"}

    PROBLEMS = {
        "unknown_strategy": (
            "Unknown plan strategy {strategy!r} for {name!r}. Entry skipped."
        ),
        "unknown_adapter": (
            "Unknown plan adapter {adapter!r} for {name!r}. Entry skipped."
        ),
        "unloaded_adapter": (
            "Plan adapter {adapter!r} for {name!r} is not loaded in this process. "
            "Entry skipped."
        ),
        "broken_adapter": (
            "Plan adapter {adapter!r} could not be imported ({error}). "
            "Its entries are skipped."
        ),
        "unsupported_strategy": (
            "Plan adapter {adapter!r} does not support strategy {strategy!r} "
            "for {name!r}. Entry skipped."
        ),
        "queues_required": (
            "Plan adapter {adapter!r} for {name!r} requires named queues. "
            "Entry skipped."
        ),
        "queues_not_a_list": (
            "Plan queues for {name!r} must be an array. Entry skipped."
        ),
        "no_valid_queues": (
            "Plan queue list for {name!r} had no valid names. Entry skipped."
        ),
    }

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.name = _text(raw.get("name"))
        self.strategy = _text(raw.get("strategy"))
        self.adapter = _text(raw.get("adapter"))
        self._macro: Any = None
        self._connection: dict[str, Any] | None = None
        self._valid_queues = self._read_queues()
        self.problem = (
            self._local_problem() if self.local() else self._adapter_problem()
        )

    def local(self) -> bool:
        return self.adapter == ""

    def sampleable(self) -> bool:
        return not self.local() and self.problem is None

    def problem_message(self) -> str:
        return self.PROBLEMS[str(self.problem)].format(
            name=self.name,
            strategy=self.strategy,
            adapter=self.adapter,
            error=format_error(self._macro) if self.problem == "broken_adapter" else "",
        )

    def key(self) -> tuple[str, ...]:
        if self.problem == "broken_adapter":
            return (self.adapter,)
        return (self.name, self.adapter, self.strategy)

    def queues(self) -> list[str]:
        return self._valid_queues[: plan.MAX_QUEUES]

    def truncated(self) -> bool:
        return len(self._valid_queues) > plan.MAX_QUEUES

    def source(self) -> str | None:
        return self._macro.plan_source(**self._connection_options())

    def call(self) -> Any:
        sample = getattr(self._macro, self.METHODS[self.strategy])
        return sample(*self.queues(), **self._options())

    def samples_working(self) -> bool:
        return hasattr(self._macro, "job_queue_working") and (
            self.strategy == JQL or self._options().get("skip_working") is True
        )

    def working(self) -> Any:
        return self._macro.job_queue_working(
            *self.queues(), **self._connection_options()
        )

    def _local_problem(self) -> str | None:
        return None if self.strategy in self.METHODS else "unknown_strategy"

    def _adapter_problem(self) -> str | None:
        if self.adapter not in plan.ADAPTERS:
            return "unknown_adapter"
        self._macro = plan.load_macro(self.adapter)
        if self._macro is None:
            return "unloaded_adapter"
        if isinstance(self._macro, Exception):
            return "broken_adapter"
        if not self._macro.supports_plan_strategy(self.strategy):
            return "unsupported_strategy"
        if self._macro.queues_required() and not self._valid_queues:
            return "queues_required"

        listed = self._raw.get("queues")
        if listed is not None and not isinstance(listed, list):
            return "queues_not_a_list"
        if listed and not self._valid_queues:
            return "no_valid_queues"
        return None

    def _options(self) -> dict[str, Any]:
        return {
            **self._macro.plan_options(self.strategy, self._raw.get("options")),
            **self._connection_options(),
        }

    def _connection_options(self) -> dict[str, Any]:
        if self._connection is None:
            self._connection = self._macro.plan_connection_options()
        return self._connection

    def _read_queues(self) -> list[str]:
        listed = self._raw.get("queues")
        names = (
            [_text(queue).strip() for queue in listed]
            if isinstance(listed, list)
            else []
        )
        return [
            name
            for name in names
            if name and len(name.encode("utf-8")) <= plan.MAX_QUEUE_NAME_BYTES
        ]
