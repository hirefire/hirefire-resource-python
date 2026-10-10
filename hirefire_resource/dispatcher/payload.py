import time
from collections.abc import Callable
from typing import Any

from hirefire_resource.buffer import Buffer
from hirefire_resource.strategy import RQT, rqt

RQT_BACKFILL_LIMIT = 60
METRIC_VALUE_LIMIT = 1e15

Series = dict[str, dict[str, dict[int, Any]]]


def build(
    data: Series,
    *,
    liveness: str | None,
    since: int | None,
    trace: dict[str, Any] | None,
    omitted: Callable[[str, str], None],
) -> tuple[list[dict[str, Any]], int | None]:
    series: Series = {}
    watermark = None

    if liveness is not None:
        claimed = _claim(data.get(liveness, {}).get(RQT, {}), since)
        series[liveness] = {RQT: claimed}
        watermark = max(claimed)

    for name, strategies in data.items():
        for strategy, buckets in strategies.items():
            series.setdefault(name, {}).setdefault(strategy, buckets)

    entries: list[dict[str, Any]] = []
    for name, strategies in series.items():
        metrics = {
            strategy: leaves
            for strategy, buckets in strategies.items()
            if (leaves := _encode(name, strategy, buckets, omitted))
        }
        if metrics:
            entries.append({"name": name, "metrics": metrics})

    if trace is not None and entries:
        entries[0]["sample_trace"] = trace
    return entries, watermark


def traced(entries: list[dict[str, Any]]) -> bool:
    return "sample_trace" in entries[0]


def without_trace(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first = {key: value for key, value in entries[0].items() if key != "sample_trace"}
    return [first, *entries[1:]]


def _claim(buckets: dict[int, Any], since: int | None) -> dict[int, Any]:
    now = int(time.time())
    start = now if since is None else since + 1
    claimed = dict(buckets)
    for second in range(min(max(start, now - RQT_BACKFILL_LIMIT), now), now + 1):
        claimed.setdefault(second, Buffer.EMPTY_BUCKET)
    return claimed


def _encode(
    name: str,
    strategy: str,
    buckets: dict[int, Any],
    omitted: Callable[[str, str], None],
) -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    for second, bucket in buckets.items():
        leaf = _rqt_leaf(bucket) if rqt(strategy) else _value_leaf(bucket)
        if leaf is None:
            omitted(name, strategy)
        else:
            leaves[str(second)] = leaf
    return leaves


def _rqt_leaf(bucket: dict[str, Any]) -> list[Any] | None:
    if bucket["count"] == 0:
        return []

    mean = _value_leaf(bucket["sum"] / bucket["count"])
    return None if mean is None else [mean, bucket["count"]]


def _value_leaf(value: float) -> float | None:
    return value if 0 <= value <= METRIC_VALUE_LIMIT else None
