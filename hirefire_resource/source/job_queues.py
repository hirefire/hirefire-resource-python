from collections.abc import Iterator

from hirefire_resource.source.job_queue import JobQueue


class JobQueues:
    def __init__(self) -> None:
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
