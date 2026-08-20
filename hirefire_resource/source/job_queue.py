from hirefire_resource._types import Sampler


class JobQueue:
    """Job-queue source: one declared local sampler for a process name (feeds ``jql`` / ``jqs``).

    Samples a job backend queue (depth or oldest age), not an individual job.

    Attributes:
        name: The process name this source reports under.
    """

    def __init__(self, name: str, sampler: Sampler) -> None:
        self.name = str(name)
        self._sampler = sampler

    def sample(self) -> float:
        """Returns the current job-queue metric value from the configured sampler."""
        return self._sampler()
