import asyncio
import functools
import os
import time
from datetime import datetime

import redis


def job_queue_latency(*queues: str, redis_url: str | None = None) -> float:
    """Maximum job queue latency across the given RQ queues.

    With no queues, measures latency across every queue present. Includes ready queue
    jobs and **due** scheduled jobs (score ≤ now). Future scheduled jobs are ignored.
    Redis is chosen from ``redis_url``, then the standard env vars, then a local default.

    Args:
        *queues (str): Names of the queues for latency measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        float: The maximum latency in seconds across the specified queues.

    Examples:
        >>> job_queue_latency()
        22.918
        >>> job_queue_latency("default")
        10.172
        >>> job_queue_latency("default", "mailer")
        22.918
        >>> job_queue_latency("default", redis_url="redis://localhost:6379/0")
        10.172
    """
    redis_url = (
        redis_url
        or os.getenv("REDIS_TLS_URL")
        or os.getenv("REDIS_URL")
        or os.getenv("REDISTOGO_URL")
        or os.getenv("REDISCLOUD_URL")
        or os.getenv("OPENREDIS_URL")
        or "redis://localhost:6379/0"
    )

    redis_client = redis.Redis.from_url(redis_url)

    queue_names: set[str] | tuple[str, ...] = queues
    if not queue_names:
        keys = redis_client.keys("rq:scheduled:*") + redis_client.keys("rq:queue:*")
        queue_names = set(_as_str(key).split(":")[2] for key in keys)

    pipeline = redis_client.pipeline()
    current_time = time.time()

    for queue in queue_names:
        pipeline.lindex(f"rq:queue:{queue}", 0)
        pipeline.zrangebyscore(
            f"rq:scheduled:{queue}",
            "-inf",
            current_time,
            withscores=True,
            start=0,
            num=1,
        )

    job_ids = pipeline.execute()

    for job_id in job_ids[::2]:
        if job_id:
            pipeline.hget(f"rq:job:{_as_str(job_id)}", "enqueued_at")

    enqueued_at_times = pipeline.execute()

    max_latency = 0.0

    for enqueued_at in enqueued_at_times:
        if enqueued_at:
            latency = current_time - _iso_to_unix(_as_str(enqueued_at))
            max_latency = max(max_latency, latency)

    for job_data in job_ids[1::2]:
        if job_data:
            job_id, score = job_data[0]
            if score < current_time:
                latency = current_time - score
                max_latency = max(max_latency, latency)

    return max_latency


async def async_job_queue_latency(*queues: str, redis_url: str | None = None) -> float:
    """Async wrapper for :func:`job_queue_latency`.

    Runs the synchronous Redis I/O in a thread pool so it does not block the event loop.

    Args:
        *queues (str): Names of the queues for latency measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        float: The maximum latency in seconds across the specified queues.

    Examples:
        >>> await async_job_queue_latency()
        22.918
        >>> await async_job_queue_latency("default")
        10.172
        >>> await async_job_queue_latency("default", "mailer")
        22.918
        >>> await async_job_queue_latency("default", redis_url="redis://localhost:6379/0")
        10.172
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_latency, *queues, redis_url=redis_url)
    return await loop.run_in_executor(None, func)


def job_queue_size(*queues: str, redis_url: str | None = None) -> int:
    """Total job count across the given RQ queues.

    With no queues, measures size across every queue present. Counts ready queue jobs
    plus **due** scheduled jobs (score ≤ now). Future scheduled jobs are excluded.
    Redis is chosen from ``redis_url``, then the standard env vars, then a local default.

    Args:
        *queues (str): Names of the queues for size measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        int: The cumulative job queue size across the specified queues.

    Examples:
        >>> job_queue_size()
        127
        >>> job_queue_size("default")
        42
        >>> job_queue_size("default", "mailer")
        127
        >>> job_queue_size("default", redis_url="redis://localhost:6379/0")
        42
    """
    redis_url = (
        redis_url
        or os.getenv("REDIS_TLS_URL")
        or os.getenv("REDIS_URL")
        or os.getenv("REDISTOGO_URL")
        or os.getenv("REDISCLOUD_URL")
        or os.getenv("OPENREDIS_URL")
        or "redis://localhost:6379/0"
    )

    redis_client = redis.Redis.from_url(redis_url)

    queue_names: set[str] | tuple[str, ...] = queues
    if not queue_names:
        keys = redis_client.keys("rq:scheduled:*") + redis_client.keys("rq:queue:*")
        queue_names = set(_as_str(key).split(":")[2] for key in keys)

    pipeline = redis_client.pipeline()
    current_time = int(time.time())

    for queue in queue_names:
        pipeline.llen(f"rq:queue:{queue}")
        pipeline.zcount(f"rq:scheduled:{queue}", 0, current_time)

    job_counts = pipeline.execute()
    total_jobs = sum(job_counts)

    return total_jobs


async def async_job_queue_size(*queues: str, redis_url: str | None = None) -> int:
    """Async wrapper for :func:`job_queue_size`.

    Runs the synchronous Redis I/O in a thread pool so it does not block the event loop.

    Args:
        *queues (str): Names of the queues for size measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        int: The cumulative job queue size across the specified queues.

    Examples:
        >>> await async_job_queue_size()
        127
        >>> await async_job_queue_size("default")
        42
        >>> await async_job_queue_size("default", "mailer")
        127
        >>> await async_job_queue_size("default", redis_url="redis://localhost:6379/0")
        42
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_size, *queues, redis_url=redis_url)
    return await loop.run_in_executor(None, func)


def _as_str(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _iso_to_unix(iso_time: str) -> float:
    dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    unix_time = float(dt.timestamp())

    return unix_time
