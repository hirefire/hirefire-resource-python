import asyncio
import functools
import os
import time
from datetime import datetime

import redis


def job_queue_latency(*queues, redis_url=None):
    """
    Calculates the maximum job queue latency using RQ. If no queues are specified, it measures
    latency across all available queues.

    This function dynamically selects the Redis broker based on the provided redis_url or
    environment variables, or falls back to a default local Redis URL.

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

    if not queues:
        keys = redis_client.keys("rq:scheduled:*") + redis_client.keys("rq:queue:*")
        queues = set(key.decode("utf-8").split(":")[2] for key in keys)

    pipeline = redis_client.pipeline()
    current_time = time.time()

    for queue in queues:
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
            pipeline.hget(f"rq:job:{job_id.decode('utf-8')}", "enqueued_at")

    enqueued_at_times = pipeline.execute()

    max_latency = 0.0

    for enqueued_at in enqueued_at_times:
        if enqueued_at:
            latency = current_time - _iso_to_unix(enqueued_at.decode("utf-8"))
            max_latency = max(max_latency, latency)

    for job_data in job_ids[1::2]:
        if job_data:
            job_id, score = job_data[0]
            if score < current_time:
                latency = current_time - score
                max_latency = max(max_latency, latency)

    return max_latency


async def async_job_queue_latency(*queues, redis_url=None):
    """
    Asynchronously calculates the maximum job queue latency using RQ. If no queues are specified, it
    measures latency across all available queues.

    This function is an asynchronous wrapper around the synchronous `job_queue_latency` function. It
    executes the synchronous function in a separate thread using asyncio's event loop and
    `run_in_executor` method. This ensures that the synchronous Redis I/O operations do not block
    the asyncio event loop.

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


def job_queue_size(*queues, redis_url=None):
    """
    Calculates the maximum job queue size using RQ. If no queues are specified, it measures latency
    across all available queues.

    This function dynamically selects the Redis broker based on the provided redis_url, environment
    variables, or falls back to a default local Redis URL.

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

    if not queues:
        keys = redis_client.keys("rq:scheduled:*") + redis_client.keys("rq:queue:*")
        queues = set(key.decode("utf-8").split(":")[2] for key in keys)

    pipeline = redis_client.pipeline()
    current_time = int(time.time())

    for queue in queues:
        pipeline.llen(f"rq:queue:{queue}")
        pipeline.zcount(f"rq:scheduled:{queue}", 0, current_time)

    job_counts = pipeline.execute()
    total_jobs = sum(job_counts)

    return total_jobs


async def async_job_queue_size(*queues, redis_url=None):
    """
    Asynchronously calculates the maximum job queue size using RQ. If no queues are specified, it
    measures latency across all available queues.

    This function is an asynchronous wrapper around the synchronous `job_queue_size` function. It
    executes the synchronous function in a separate thread using asyncio's event loop and
    `run_in_executor` method. This ensures that the synchronous Redis I/O operations do not block
    the asyncio event loop.

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
        >>> await async_job_queue_size("default", redis_url ="redis://localhost:6379/0")
        42
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_size, *queues, redis_url=redis_url)
    return await loop.run_in_executor(None, func)


def _iso_to_unix(iso_time):
    dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    unix_time = float(dt.timestamp())

    return unix_time
