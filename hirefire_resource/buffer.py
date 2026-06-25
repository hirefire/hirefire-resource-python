import threading
import time
from typing import TypedDict


class WorkerEntry(TypedDict):
    name: str
    sample: float


class FlushedBuffer(TypedDict):
    web: dict[int, list[int]]
    workers: list[WorkerEntry]
    cpu: dict[str, dict[int, list[float]]]


class Buffer:
    def __init__(self, ttl: int = 60) -> None:
        self._web: dict[int, list[int]] = {}
        self._workers: dict[str, float] = {}
        self._cpu: dict[str, dict[int, list[float]]] = {}
        self._mutex = threading.Lock()
        self._ttl = ttl

    def sample_web(self, sample: int) -> None:
        timestamp = int(time.time())
        with self._mutex:
            self._prune(self._web, timestamp)
            self._web.setdefault(timestamp, []).append(sample)

    def sample_worker(self, name: str, sample: float) -> None:
        with self._mutex:
            self._workers[name] = sample

    def sample_cpu(self, name: str, value: float) -> None:
        timestamp = int(time.time())
        with self._mutex:
            buckets = self._cpu.setdefault(name, {})
            self._prune(buckets, timestamp)
            buckets.setdefault(timestamp, []).append(value)

    def flush(self) -> FlushedBuffer:
        with self._mutex:
            web, workers, cpu = self._web, self._workers, self._cpu
            self._web, self._workers, self._cpu = {}, {}, {}

            return {
                "web": web,
                "workers": [
                    {"name": name, "sample": sample} for name, sample in workers.items()
                ],
                "cpu": cpu,
            }

    def repopulate_web(self, data: dict[int, list[int]]) -> None:
        now = int(time.time())
        with self._mutex:
            for timestamp, samples in data.items():
                if timestamp < now - self._ttl:
                    continue
                self._web.setdefault(timestamp, []).extend(samples)

    def _prune(self, buckets: dict[int, list], now: int) -> None:
        if len(buckets) <= self._ttl + 5:
            return

        cutoff = now - self._ttl
        for timestamp in [t for t in buckets if t < cutoff]:
            del buckets[timestamp]
