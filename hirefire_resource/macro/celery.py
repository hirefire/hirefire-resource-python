import asyncio
import functools
import json
import os
import threading
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

from hirefire_resource.errors import SampleIncompleteError, SampleNotReadyError
from hirefire_resource.log import format_error, safe_log
from hirefire_resource.plan import hooks as _plan_hooks
from hirefire_resource.utility import normalize_queues

before_sample_job_queues = _plan_hooks.before_sample_job_queues
after_sample_job_queues = _plan_hooks.after_sample_job_queues
supports_plan_strategy = _plan_hooks.supports_plan_strategy

_PLAN_OPTION_SCHEMA = {"jqs": {"skip_working": "boolean"}}
_HELD_TASKS_INSPECT_TIMEOUT = 1.0
_HELD_TASKS_REFRESH_INTERVAL = 5.0
_HELD_TASKS_MAX_AGE = 30.0
_HELD_TASKS_IDLE_TIMEOUT = 60.0
_HELD_TASKS_NOT_READY = "Celery has not counted the tasks its workers hold yet."

_held_tasks_lock = threading.Lock()
_held_tasks: dict[object, "_HeldTasks"] = {}


def plan_options(strategy: object, options: object) -> dict[str, Any]:
    return _plan_hooks.extract_plan_options(strategy, options, _PLAN_OPTION_SCHEMA)


def reinit_after_fork() -> None:
    global _held_tasks_lock, _held_tasks
    _held_tasks_lock = threading.Lock()
    _held_tasks = {}


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


@contextmanager
def _caller_connection(app: Any) -> Iterator[Any]:
    with app.connection_or_acquire() as connection:
        connection.connect()
        yield connection


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
    skip_working: bool = False,
) -> int:
    queue_names = normalize_queues(*queues, allow_empty=False)
    app, owned = _sample_app(broker_url, celery_app)
    conn_cm = _sample_connection(app) if owned else _caller_connection(app)

    with conn_cm as connection:
        with connection.channel() as channel:
            size = _job_queue_size_broker(app, channel, queue_names)

    if skip_working:
        return size
    return size + _held_task_count(app, owned, queue_names)


async def async_job_queue_size(
    *queues: str,
    broker_url: str | None = None,
    celery_app: "Celery | None" = None,
    skip_working: bool = False,
) -> int:
    return await asyncio.to_thread(
        job_queue_size,
        *queues,
        broker_url=broker_url,
        celery_app=celery_app,
        skip_working=skip_working,
    )


def job_queue_working(
    *queues: str,
    broker_url: str | None = None,
    celery_app: "Celery | None" = None,
) -> int:
    queue_names = normalize_queues(*queues, allow_empty=False)
    app, owned = _sample_app(broker_url, celery_app)
    return _held_task_count(app, owned, queue_names)


async def async_job_queue_working(
    *queues: str,
    broker_url: str | None = None,
    celery_app: "Celery | None" = None,
) -> int:
    return await asyncio.to_thread(
        job_queue_working, *queues, broker_url=broker_url, celery_app=celery_app
    )


def _sample_app(
    broker_url: str | None, celery_app: "Celery | None"
) -> tuple[Any, bool]:
    if celery_app is not None and broker_url is not None:
        raise ValueError(
            "Cannot specify both 'celery_app' and 'broker_url'. "
            "Use 'celery_app' to pass your configured Celery app (recommended for priority queues), "
            "or 'broker_url' for simple setups."
        )
    if celery_app is None:
        return _owned_celery_app(broker_url), True
    return celery_app, False


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


class _HeldTasks:
    def __init__(self, key: object, app: Any, owned: bool) -> None:
        self._key = key
        self._app = app
        self._owned = owned
        self._counts: dict[str, int] | None = None
        self._counted_at = 0.0
        self._started_at = self._asked_at = time.monotonic()
        self._error: str | None = None
        self.thread = threading.Thread(
            target=self._run, name="hirefire-celery-held-tasks", daemon=True
        )
        if owned:
            app.control.mailbox.producer_pool = None

    def count(self, queues: set[str]) -> int:
        now = time.monotonic()
        self._asked_at = now
        if self._counts is not None:
            if now - self._counted_at <= _HELD_TASKS_MAX_AGE:
                return sum(self._counts.get(queue, 0) for queue in queues)
        elif self._error is None and now - self._started_at <= _HELD_TASKS_MAX_AGE:
            raise SampleNotReadyError(_HELD_TASKS_NOT_READY)
        reason = f" The last refresh raised {self._error}." if self._error else ""
        raise SampleIncompleteError(
            "Celery has no recent count of the tasks its workers hold." + reason
        )

    def _run(self) -> None:
        while True:
            started = time.monotonic()
            self._refresh()
            elapsed = time.monotonic() - started
            threading.Event().wait(max(0.0, _HELD_TASKS_REFRESH_INTERVAL - elapsed))
            with _held_tasks_lock:
                if _held_tasks.get(self._key) is not self:
                    return
                if time.monotonic() - self._asked_at >= _HELD_TASKS_IDLE_TIMEOUT:
                    del _held_tasks[self._key]
                    return

    def _refresh(self) -> None:
        try:
            counts = _inspect_held_tasks(self._app, self._owned)
        except Exception as error:
            with _held_tasks_lock:
                self._error = format_error(error)
            return
        with _held_tasks_lock:
            self._counts = counts
            self._counted_at = time.monotonic()
            self._error = None


def _held_task_count(app: Any, owned: bool, queues: set[str]) -> int:
    key: object = app.conf.broker_url if owned else id(app)
    with _held_tasks_lock:
        held = _held_tasks.get(key)
        if held is not None and held.thread.is_alive():
            return held.count(queues)
        held = _held_tasks[key] = _HeldTasks(key, app, owned)
        held.thread.start()

    from hirefire_resource.hirefire import HireFire

    safe_log(
        HireFire.configuration.logger,
        "info",
        "[HireFire] Counting the tasks Celery workers hold. "
        "Samples that need the count start when the first count arrives.",
    )
    raise SampleNotReadyError(_HELD_TASKS_NOT_READY)


def _inspect_held_tasks(app: Any, owned: bool) -> dict[str, int]:
    connect = _sample_connection(app) if owned else _caller_connection(app)
    with connect as connection:
        inspect = app.control.inspect(
            timeout=_HELD_TASKS_INSPECT_TIMEOUT, connection=connection
        )
        collections = (inspect.active(), inspect.reserved(), inspect.scheduled())

    now = time.time()
    counts: dict[str, int] = {}
    for collection in collections:
        if collection is None:
            continue
        for tasks in collection.values():
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                queue = _held_task_queue(task, now)
                if queue is not None:
                    counts[queue] = counts.get(queue, 0) + 1
    return counts


def _held_task_queue(task: Any, now: float) -> str | None:
    try:
        eta = task.get("eta")
        if eta:
            if now < parse(eta).timestamp():
                return None
            task = task["request"]
        queue = task["delivery_info"]["routing_key"]
    except Exception:
        return None
    return queue if isinstance(queue, str) else None


def plan_connection_options() -> dict[str, Any]:
    from hirefire_resource.identity import presence

    url = presence(os.environ.get("HIREFIRE_CELERY_BROKER_URL"))
    return {"broker_url": url} if url else {}
