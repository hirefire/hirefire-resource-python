from hirefire_resource._types import Sampler


class JobQueue:
    def __init__(self, name: str, sampler: Sampler) -> None:
        self.name = str(name)
        self._sampler = sampler

    def sample(self) -> float:
        return self._sampler()
