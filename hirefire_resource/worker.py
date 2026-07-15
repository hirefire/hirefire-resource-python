from hirefire_resource._types import Sampler


class Worker:
    """Job-metric collector for a declared worker process.

    Attributes:
        name: The process name this collector reports under.
    """

    def __init__(self, name: str, sampler: Sampler) -> None:
        self.name = str(name)
        self._sampler = sampler

    def sample(self) -> float:
        """Returns the current job metric value from the configured sampler."""
        return self._sampler()
