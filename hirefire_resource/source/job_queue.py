import asyncio
from collections.abc import Coroutine

from hirefire_resource._types import Sampler


class JobQueue:
    def __init__(self, name: str, sampler: Sampler) -> None:
        self.name = str(name)
        self._sampler = sampler

    def sample(self) -> float:
        value = self._sampler()
        if isinstance(value, Coroutine):
            return asyncio.run(value)
        return value
