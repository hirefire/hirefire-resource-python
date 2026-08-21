import math
import threading
import time
from typing import Any


class Buffer:
    SAMPLE_COUNT_LIMIT = 1_000_000

    def __init__(self, ttl: int = 60) -> None:
        self._metrics: dict[str, dict[str, dict[int, Any]]] = {}
        self._mutex = threading.Lock()
        self._ttl = ttl

    def sample(self, name: str, strategy: str, value: float) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return
        if not math.isfinite(value):
            return

        timestamp = int(time.time())
        strategy = str(strategy)
        with self._mutex:
            series = self._series_for(name, strategy)
            self._prune(series, timestamp)
            if strategy == "rqt":
                bucket = series.get(timestamp)
                if bucket is None:
                    bucket = {"sum": 0.0, "count": 0}
                    series[timestamp] = bucket
                if bucket["count"] >= self.SAMPLE_COUNT_LIMIT:
                    return
                bucket["sum"] += float(value)
                bucket["count"] += 1
            else:
                series[timestamp] = value

    def flush(self) -> dict[str, dict[str, dict[int, Any]]]:
        with self._mutex:
            metrics = self._metrics
            self._metrics = {}
            return metrics

    def discard_inherited(self) -> None:
        with self._mutex:
            self._metrics = {}

    def reinit_after_fork(self) -> None:
        self._mutex = threading.Lock()
        self._metrics = {}

    def reinit_locks_after_fork(self) -> None:
        self._mutex = threading.Lock()

    def _reinit_after_fork(self) -> None:
        self.reinit_after_fork()

    def repopulate(self, name: str, strategy: str, data: dict[int, Any]) -> None:
        strategy = str(strategy)
        if strategy != "rqt":
            return

        now = int(time.time())
        with self._mutex:
            series = None
            for timestamp, bucket in data.items():
                if timestamp < now - self._ttl:
                    continue
                sum_v, count = self._rqt_parts(bucket)
                if count <= 0:
                    continue
                if series is None:
                    series = self._series_for(name, strategy)
                existing = series.get(timestamp)
                if isinstance(existing, dict):
                    series[timestamp] = self._clamp_rqt(
                        existing["sum"] + sum_v, existing["count"] + count
                    )
                else:
                    series[timestamp] = self._clamp_rqt(sum_v, count)
            if series is not None:
                self._prune(series, now)

    def _series_for(self, name: str, strategy: str) -> dict[int, Any]:
        by_name = self._metrics.setdefault(name, {})
        return by_name.setdefault(strategy, {})

    def _clamp_rqt(self, sum_v: float, count: int) -> dict[str, Any]:
        if count > self.SAMPLE_COUNT_LIMIT:
            mean = sum_v / count
            return {
                "sum": mean * self.SAMPLE_COUNT_LIMIT,
                "count": self.SAMPLE_COUNT_LIMIT,
            }
        return {"sum": sum_v, "count": count}

    @staticmethod
    def _rqt_parts(bucket: Any) -> tuple[float, int]:
        if isinstance(bucket, dict):
            sum_v = bucket["sum"] if "sum" in bucket else 0.0
            count = bucket["count"] if "count" in bucket else 0
            return float(sum_v), int(count)
        return 0.0, 0

    def _prune(self, buckets: dict[int, Any], now: int) -> None:
        if len(buckets) <= self._ttl + 5:
            return

        cutoff = now - self._ttl
        for timestamp in [t for t in buckets if t < cutoff]:
            del buckets[timestamp]
