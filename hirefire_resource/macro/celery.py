import asyncio
import functools
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, TypeVar, cast

from celery import Celery
from celery.signals import before_task_publish
from dateutil.parser import parse

try:
    from amqp.exceptions import ChannelError

    AMQP_AVAILABLE = True
except ImportError:

    class ChannelError(Exception):  # type: ignore[no-redef]
        pass

    AMQP_AVAILABLE = False

from hirefire_resource.plan import hooks as _plan_hooks
from hirefire_resource.utility import normalize_queues

before_sample_job_queues = _plan_hooks.before_sample_job_queues
after_sample_job_queues = _plan_hooks.after_sample_job_queues
reinit_after_fork = _plan_hooks.reinit_after_fork
plan_options = _plan_hooks.plan_options
supports_plan_strategy = _plan_hooks.supports_plan_strategy


def queues_required() -> bool:
    return True


def _get_queue_arguments_from_app(
    app: Any, queues: set[str] | tuple[str, ...]
) -> dict[str, Any]:
    queue_args = {}
    task_queues = getattr(app.conf, "task_queues", None) or []
    for q in task_queues:
        queue_name = getattr(q, "name", None)
        if queue_name and queue_name in queues:
            queue_args[queue_name] = getattr(q, "queue_arguments", None)
    return queue_args


_F = TypeVar("_F", bound=Callable[..., Any])


def mitigate_connection_reset_error(
    retries: int = 2, delay: float = 0
) -> Callable[[_F], _F]:
    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempts = max(1, retries)
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except ConnectionResetError:
                    if attempt >= attempts - 1:
                        raise
                    if delay:
                        time.sleep(delay)

        return cast(_F, wrapper)

    return decorator


_SAMPLE_BROKER_TIMEOUT = 5.0


def _sample_transport_options(url: str) -> dict[str, float]:
    scheme = url.split(":", 1)[0].lower()
    if scheme in ("redis", "rediss"):
        return {
            "socket_timeout": _SAMPLE_BROKER_TIMEOUT,
            "socket_connect_timeout": _SAMPLE_BROKER_TIMEOUT,
        }
    if scheme in ("amqp", "amqps", "pyamqp"):
        return {
            "read_timeout": _SAMPLE_BROKER_TIMEOUT,
            "write_timeout": _SAMPLE_BROKER_TIMEOUT,
        }
    return {
        "socket_timeout": _SAMPLE_BROKER_TIMEOUT,
        "socket_connect_timeout": _SAMPLE_BROKER_TIMEOUT,
        "read_timeout": _SAMPLE_BROKER_TIMEOUT,
        "write_timeout": _SAMPLE_BROKER_TIMEOUT,
    }


def _owned_celery_app(broker_url: str | None = None) -> Celery:
    url = _resolve_broker_url(broker_url)
    app = Celery(broker=url)
    app.conf.broker_pool_limit = None
    app.conf.broker_transport_options = _sample_transport_options(url)
    app.conf.broker_connection_timeout = _SAMPLE_BROKER_TIMEOUT
    app.conf.broker_connection_retry = False
    app.conf.broker_connection_retry_on_startup = False
    app.conf.broker_connection_max_retries = 0
    return app


@contextmanager
def _sample_connection(app: Celery) -> Iterator[Any]:
    connection = app.connection()
    try:
        connection._ensure_connection(
            max_retries=0,
            interval_start=0,
            reraise_as_library_errors=True,
        )
        yield connection
    finally:
        connection.release()


@mitigate_connection_reset_error()
def job_queue_latency(*queues: str, broker_url: str | None = None) -> float:
    queue_names = normalize_queues(*queues, allow_empty=False)
    app = _owned_celery_app(broker_url)

    with _sample_connection(app) as connection:
        with connection.channel() as channel:
            if hasattr(channel, "_size"):
                fn = _job_queue_latency_redis
            else:
                fn = _job_queue_latency_rabbitmq

            return float(max(fn(channel, queue) for queue in queue_names))


async def async_job_queue_latency(*queues: str, broker_url: str | None = None) -> float:
    return await asyncio.to_thread(job_queue_latency, *queues, broker_url=broker_url)


@mitigate_connection_reset_error()
def job_queue_size(
    *queues: str,
    broker_url: str | None = None,
    celery_app: "Celery | None" = None,
) -> int:
    queue_names = normalize_queues(*queues, allow_empty=False)

    if celery_app is not None and broker_url is not None:
        raise ValueError(
            "Cannot specify both 'celery_app' and 'broker_url'. "
            "Use 'celery_app' to pass your configured Celery app (recommended for priority queues), "
            "or 'broker_url' for simple setups."
        )

    if celery_app is None:
        app = _owned_celery_app(broker_url)
        conn_cm = _sample_connection(app)
    else:
        app = celery_app
        conn_cm = app.connection_or_acquire()

    with conn_cm as connection:
        with connection.channel() as channel:
            return _job_queue_size_broker(app, channel, queue_names)


async def async_job_queue_size(
    *queues: str,
    broker_url: str | None = None,
    celery_app: "Celery | None" = None,
) -> int:
    return await asyncio.to_thread(
        job_queue_size, *queues, broker_url=broker_url, celery_app=celery_app
    )


@before_task_publish.connect
def run_at_header_signal(
    sender: Any = None,
    headers: Any = None,
    body: Any = None,
    properties: Any = None,
    **kwargs: Any,
) -> None:
    headers = headers or {}
    eta = headers.get("eta")

    if eta:
        headers["run_at"] = eta
    else:
        headers["run_at"] = datetime.now(timezone.utc).isoformat()


def _job_queue_latency_redis(channel: Any, queue: str) -> float:
    oldest_job = channel.client.lindex(queue, -1)

    if not oldest_job:
        return 0.0

    try:
        oldest_job = json.loads(_as_str(oldest_job))
        run_at = oldest_job.get("headers", {}).get("run_at")

        if run_at:
            run_at_time = parse(run_at)
            latency = time.time() - run_at_time.timestamp()
            return max(0.0, latency)
    except Exception:
        return 0.0

    return 0.0


def _as_str(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _resolve_broker_url(broker_url: str | None) -> str:
    if broker_url:
        return broker_url

    for key in (
        "AMQP_URL",
        "RABBITMQ_URL",
        "RABBITMQ_BIGWIG_URL",
        "CLOUDAMQP_URL",
        "REDIS_TLS_URL",
        "REDIS_URL",
        "REDISTOGO_URL",
        "REDISCLOUD_URL",
        "OPENREDIS_URL",
    ):
        value = os.environ.get(key)
        if value:
            return value

    if AMQP_AVAILABLE:
        return "amqp://guest:guest@localhost:5672"
    return "redis://localhost:6379/0"


def _job_queue_latency_rabbitmq(channel: Any, queue: str) -> float:
    try:
        message = channel.basic_get(queue)

        if message is None:
            return 0.0

        try:
            run_at = message.headers.get("run_at")

            if run_at:
                run_at_time = parse(run_at)
                latency = time.time() - run_at_time.timestamp()
                return max(0.0, latency)

            return 0.0
        except Exception:
            return 0.0
        finally:
            channel.basic_reject(message.delivery_tag, requeue=True)
    except ChannelError:
        return 0.0


def _job_queue_size_broker(
    app: Any, channel: Any, queues: set[str] | tuple[str, ...]
) -> int:
    if hasattr(channel, "_size"):
        return sum(_job_queue_size_redis(channel, queue) for queue in queues)
    else:
        queue_args = _get_queue_arguments_from_app(app, queues)
        return sum(
            _job_queue_size_rabbitmq(channel, queue, queue_args.get(queue))
            for queue in queues
        )


def _job_queue_size_redis(channel: Any, queue: str) -> int:
    return channel.client.llen(queue)


def _job_queue_size_rabbitmq(channel: Any, queue: str, arguments: Any = None) -> int:
    try:
        return channel.queue_declare(
            queue=queue, passive=True, arguments=arguments
        ).message_count
    except ChannelError:
        return 0


def plan_connection_options() -> dict[str, Any]:
    from hirefire_resource.identity import presence

    url = presence(os.environ.get("HIREFIRE_CELERY_BROKER_URL"))
    return {"broker_url": url} if url else {}
