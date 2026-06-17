import threading
import time


class Buffer:
    """Thread-safe storage for web, worker, and CPU metric samples."""

    def __init__(self, ttl=60):
        self._web = {}
        self._workers = {}
        self._cpu = {}
        self._mutex = threading.Lock()
        self._ttl = ttl

    def sample_web(self, sample):
        timestamp = int(time.time())
        with self._mutex:
            self._prune(self._web, timestamp)
            self._web.setdefault(timestamp, []).append(sample)

    # Latest-wins per name: worker samples are gauges, so only the most recent matters.
    def sample_worker(self, name, sample):
        with self._mutex:
            self._workers[name] = sample

    def sample_cpu(self, name, value):
        timestamp = int(time.time())
        with self._mutex:
            buckets = self._cpu.setdefault(name, {})
            self._prune(buckets, timestamp)
            buckets.setdefault(timestamp, []).append(value)

    def flush(self):
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

    def repopulate_web(self, data):
        now = int(time.time())
        with self._mutex:
            for timestamp, samples in data.items():
                if timestamp < now - self._ttl:
                    continue
                self._web.setdefault(timestamp, []).extend(samples)

    def _prune(self, buckets, now):
        if len(buckets) <= self._ttl + 5:
            return

        cutoff = now - self._ttl
        for timestamp in [t for t in buckets if t < cutoff]:
            del buckets[timestamp]
