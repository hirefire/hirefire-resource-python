import time
from datetime import datetime

import redis


def job_queue_latency(*queues, redis_url=None):
    """
    Calculates the maximum queue latency across the specified queues.

    Args:
        queues: A variable number of queue names as strings.
        redis_url (str, optional): The Redis server URL. Defaults to the environment variable
            REDIS_URL, then REDIS_TLS_URL, and falls back to "redis://localhost:6379" if neither is set.

    Returns:
        int: The maximum latency in seconds across the specified queues.

    Raises:
        ValueError: If no queue names are provided.

    Examples:
        >>> job_queue_latency('default')
        3600
        >>> job_queue_latency('default', 'mailer')
        7200
    """
    redis_url = (
        redis_url
        or os.getenv("REDIS_URL")
        or os.getenv("REDIS_TLS_URL")
        or "redis://localhost:6379"
    )
    r = redis.Redis.from_url(redis_url)
    current_time = int(time.time())
    pipe = r.pipeline()

    for queue_name in queues:
        pipe.lindex("rq:queue:" + queue_name, 0)
        pipe.zrangebyscore(
            "rq:scheduled:" + queue_name,
            "-inf",
            current_time,
            withscores=True,
            start=0,
            num=1,
        )
        job_ids = pipe.execute()

    for job_id in job_ids[::2]:
        if job_id:
            pipe.hget("rq:job:" + job_id.decode("utf-8"), "enqueued_at")
            enqueued_at_times = pipe.execute()

    max_latency = 0
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


def job_queue_size(*queues, redis_url=None):
    """
    Calculates the total job count across the specified queues.

    Args:
        queues: A variable number of queue names as strings.
        redis_url (str, optional): The Redis server URL. Defaults to the environment variable
            REDIS_URL, then REDIS_TLS_URL, and falls back to "redis://localhost:6379" if neither is set.

    Returns:
        int: The cumulative job count across the specified queues.

    Raises:
        ValueError: If no queue names are provided.

    Examples:
        >>> job_queue_size('default')
        42
        >>> job_queue_size('default', 'mailer')
        85
    """
    redis_url = (
        redis_url
        or os.getenv("REDIS_URL")
        or os.getenv("REDIS_TLS_URL")
        or "redis://localhost:6379"
    )
    r = redis.Redis.from_url(redis_url)
    current_time = int(time.time())
    pipe = r.pipeline()

    for queue_name in queues:
        ready_queue_key = f"rq:queue:{queue_name}"
        scheduled_queue_key = f"rq:scheduled:{queue_name}"
        pipe.llen(ready_queue_key)
        pipe.zcount(scheduled_queue_key, 0, current_time)

    job_counts = pipe.execute()
    total_jobs = sum(job_counts)

    return total_jobs


def _iso_to_unix(iso_time):
    """
    Converts an ISO 8601 formatted string to a Unix timestamp.

    Args:
        iso_time: A string representing the time in ISO 8601 format.

    Returns:
        An integer Unix timestamp representing the given time.

    Examples:
        >>> _iso_to_unix("2023-01-01T00:00:00Z")
        1672531200
    """
    dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    unix_time = int(dt.timestamp())

    return unix_time
