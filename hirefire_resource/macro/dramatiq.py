import asyncio
import functools
import json
import os
import time
from typing import Any

from hirefire_resource.plan import hooks as _plan_hooks
from hirefire_resource.utility import normalize_queues

before_sample_job_queues = _plan_hooks.before_sample_job_queues
after_sample_job_queues = _plan_hooks.after_sample_job_queues
reinit_after_fork = _plan_hooks.reinit_after_fork

try:
    import redis
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - optional dep for type checkers / bare core
    redis = None

    class RedisError(Exception):  # type: ignore[no-redef]
        pass


try:
    import pika
    from pika.exceptions import AMQPChannelError, AMQPConnectionError

    PIKA_AVAILABLE = True
except ImportError:
    pika = None

    class AMQPConnectionError(Exception):  # type: ignore[no-redef]
        pass

    class AMQPChannelError(Exception):  # type: ignore[no-redef]
        pass

    PIKA_AVAILABLE = False

_HMGET_BATCH = 200
_DEFAULT_NAMESPACE = "dramatiq"
_AMQP_SCHEMES = ("amqp://", "amqps://")
_REDIS_SCHEMES = ("redis://", "rediss://")


def job_queue_size(
    *queues: str,
    broker_url: str | None = None,
    broker: object | None = None,
    namespace: str | None = None,
) -> int:
    """Total waiting job count across the given Dramatiq queues.

    Queue names are required. Waiting semantics differ by broker:

    - **Redis:** ready list length (``LLEN``) plus **due** delay-queue messages
      on ``{queue}.DQ`` whose ``options.eta`` is ≤ now (milliseconds). Retries
      that use delay share that same ``.DQ`` pool. Future delayed messages,
      dead-letter (``.XQ``), in-flight acks (working), and delay work held only
      in worker memory are excluded. Prefer Redis when delayed scale-from-zero
      matters (min = 0 with due work still on the broker).
    - **RabbitMQ:** main queue ready ``message_count`` only. ``.DQ`` and ``.XQ``
      are not counted in v1 (no non-destructive eta filter on the broker). Due
      delayed work alone will not scale workers on RabbitMQ until a consumer
      promotes it.

    Never use Dramatiq's ``do_qsize`` (it includes working/acks).

    Connection is chosen from ``broker`` / ``broker_url``, then
    ``HIREFIRE_DRAMATIQ_URL`` is only applied via plan hooks, then the standard
    AMQP then Redis env ladder, then a local default (AMQP when pika is
    available, otherwise Redis).

    Args:
        *queues (str): Canonical queue names (not ``.DQ`` / ``.XQ`` suffixes).
        broker_url (str, optional): Broker URL. Cannot be used with ``broker``.
        broker (object, optional): A live ``RedisBroker`` or ``RabbitmqBroker``.
            Cannot be used with ``broker_url``. URL-owned clients are closed
            after the sample. An injected Redis client's connection is not
            closed.
        namespace (str, optional): Redis key prefix. Defaults to the injected
            broker's namespace, then ``HIREFIRE_DRAMATIQ_NAMESPACE``, then
            ``"dramatiq"``. Ignored for RabbitMQ.

    Returns:
        int: Waiting job count across the queues. Returns 0 when the broker is
            unreachable.

    Raises:
        MissingQueueError: If no queue names are provided.
        ValueError: If both ``broker`` and ``broker_url`` are provided.
    """
    queue_names = _canonical_queues(*queues)
    resolved: dict[str, Any] | None = None
    try:
        resolved = _resolve_connection(
            broker_url=broker_url, broker=broker, namespace=namespace
        )
        if resolved["kind"] == "redis":
            return _redis_job_queue_size(
                resolved["client"], queue_names, resolved["namespace"]
            )
        return _rabbitmq_job_queue_size(resolved["connection"], queue_names)
    except (RedisError, AMQPConnectionError, OSError):
        return 0
    finally:
        if resolved is not None:
            _close_resolved(resolved)


async def async_job_queue_size(
    *queues: str,
    broker_url: str | None = None,
    broker: object | None = None,
    namespace: str | None = None,
) -> int:
    """Async wrapper for :func:`job_queue_size`.

    Runs the synchronous broker I/O in a thread pool so it does not block the
    event loop. Same arguments, return value, and Redis vs RabbitMQ notes as
    :func:`job_queue_size`.
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(
        job_queue_size,
        *queues,
        broker_url=broker_url,
        broker=broker,
        namespace=namespace,
    )
    return await loop.run_in_executor(None, func)


def job_queue_latency(
    *queues: str,
    broker_url: str | None = None,
    broker: object | None = None,
    namespace: str | None = None,
) -> float:
    """Maximum job queue latency across the given Dramatiq queues (seconds).

    Queue names are required. Age rules:

    - **Redis live head:** ``(now_ms - message_timestamp) / 1000`` from the
      oldest ready id (``LINDEX 0`` on the FIFO ready list).
    - **Redis due delayed:** ``(now_ms - eta) / 1000`` for the earliest due
      ``options.eta`` on ``{queue}.DQ``.
    - **RabbitMQ:** main queue head age via ``basic_get`` + requeue and
      ``message_timestamp``. Occasional requeue reorder is possible (same class
      of caveat as Celery AMQP JQL). Prefer Redis when JQL accuracy matters.
      Delayed / ``.DQ`` age is not measured on RabbitMQ in v1.

    Stock Dramatiq messages always carry ``message_timestamp`` (milliseconds).
    No HireFire publisher middleware is required.

    Args:
        *queues (str): Canonical queue names.
        broker_url (str, optional): Broker URL. Cannot be used with ``broker``.
        broker (object, optional): A live ``RedisBroker`` or ``RabbitmqBroker``.
        namespace (str, optional): Redis key prefix (see :func:`job_queue_size`).

    Returns:
        float: Maximum latency in seconds across the queues. Returns 0 when
            empty or the broker is unreachable.

    Raises:
        MissingQueueError: If no queue names are provided.
        ValueError: If both ``broker`` and ``broker_url`` are provided.
    """
    queue_names = _canonical_queues(*queues)
    resolved: dict[str, Any] | None = None
    try:
        resolved = _resolve_connection(
            broker_url=broker_url, broker=broker, namespace=namespace
        )
        if resolved["kind"] == "redis":
            return _redis_job_queue_latency(
                resolved["client"], queue_names, resolved["namespace"]
            )
        return _rabbitmq_job_queue_latency(resolved["connection"], queue_names)
    except (RedisError, AMQPConnectionError, OSError):
        return 0.0
    finally:
        if resolved is not None:
            _close_resolved(resolved)


async def async_job_queue_latency(
    *queues: str,
    broker_url: str | None = None,
    broker: object | None = None,
    namespace: str | None = None,
) -> float:
    """Async wrapper for :func:`job_queue_latency`.

    Runs the synchronous broker I/O in a thread pool so it does not block the
    event loop. Same arguments, return value, and Redis vs RabbitMQ notes as
    :func:`job_queue_latency`.
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(
        job_queue_latency,
        *queues,
        broker_url=broker_url,
        broker=broker,
        namespace=namespace,
    )
    return await loop.run_in_executor(None, func)


def plan_options(strategy: object, options: object) -> dict[str, Any]:
    return {}


def plan_connection_options() -> dict[str, Any]:
    from hirefire_resource.identity import presence

    out: dict[str, Any] = {}
    url = presence(os.environ.get("HIREFIRE_DRAMATIQ_URL"))
    if url:
        out["broker_url"] = url
    namespace = presence(os.environ.get("HIREFIRE_DRAMATIQ_NAMESPACE"))
    if namespace:
        out["namespace"] = namespace
    return out


def supports_plan_strategy(strategy: object) -> bool:
    from hirefire_resource import plan

    return plan.known_strategy(strategy)


def _canonical_queues(*queues: str) -> set[str]:
    names = normalize_queues(*queues)
    return {_canonical_queue_name(name) for name in names}


def _canonical_queue_name(name: str) -> str:
    if name.endswith(".DQ") or name.endswith(".XQ"):
        return name[:-3]
    return name


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

    if PIKA_AVAILABLE:
        return "amqp://guest:guest@localhost:5672"
    return "redis://localhost:6379/0"


def _resolve_namespace(
    namespace: str | None, broker: object | None, *, redis_path: bool
) -> str:
    if namespace is not None and str(namespace).strip():
        return str(namespace).strip()
    if redis_path and broker is not None:
        broker_ns = getattr(broker, "namespace", None)
        if broker_ns is not None and str(broker_ns).strip():
            return str(broker_ns).strip()
    from hirefire_resource.identity import presence

    env_ns = presence(os.environ.get("HIREFIRE_DRAMATIQ_NAMESPACE"))
    if env_ns:
        return env_ns
    return _DEFAULT_NAMESPACE


def _is_redis_broker(broker: object) -> bool:
    return type(broker).__name__ == "RedisBroker" and type(
        broker
    ).__module__.startswith("dramatiq")


def _is_rabbitmq_broker(broker: object) -> bool:
    return type(broker).__name__ == "RabbitmqBroker" and type(
        broker
    ).__module__.startswith("dramatiq")


def _url_kind(url: str) -> str:
    lower = url.lower()
    if lower.startswith(_AMQP_SCHEMES):
        return "rabbitmq"
    if lower.startswith(_REDIS_SCHEMES):
        return "redis"
    if "amqp" in lower.split(":", 1)[0]:
        return "rabbitmq"
    return "redis"


def _resolve_connection(
    *,
    broker_url: str | None,
    broker: object | None,
    namespace: str | None,
) -> dict[str, Any]:
    if broker is not None and broker_url is not None:
        raise ValueError(
            "Cannot specify both 'broker' and 'broker_url'. "
            "Use 'broker' to pass your configured Dramatiq broker, "
            "or 'broker_url' for simple setups."
        )

    if broker is not None:
        if _is_redis_broker(broker):
            client = getattr(broker, "client", None)
            if client is None:
                raise ValueError("Dramatiq RedisBroker has no client")
            return {
                "kind": "redis",
                "client": client,
                "owned": False,
                "namespace": _resolve_namespace(namespace, broker, redis_path=True),
                "connection": None,
            }
        if _is_rabbitmq_broker(broker):
            parameters = getattr(broker, "parameters", None)
            if parameters is None or pika is None:
                raise ValueError(
                    "Dramatiq RabbitmqBroker requires pika and connection parameters"
                )
            connection = pika.BlockingConnection(parameters=parameters)
            return {
                "kind": "rabbitmq",
                "connection": connection,
                "owned": True,
                "client": None,
                "namespace": _DEFAULT_NAMESPACE,
            }
        raise ValueError(
            "Unsupported Dramatiq broker type "
            f"{type(broker).__module__}.{type(broker).__name__}. "
            "Expected RedisBroker or RabbitmqBroker."
        )

    url = _resolve_broker_url(broker_url)
    kind = _url_kind(url)

    if kind == "redis":
        if redis is None:
            raise RuntimeError("redis package is required for Dramatiq Redis sampling")
        client = redis.Redis.from_url(url)
        return {
            "kind": "redis",
            "client": client,
            "owned": True,
            "namespace": _resolve_namespace(namespace, None, redis_path=True),
            "connection": None,
        }

    if pika is None:
        raise RuntimeError("pika package is required for Dramatiq RabbitMQ sampling")
    connection = pika.BlockingConnection(pika.URLParameters(url))
    return {
        "kind": "rabbitmq",
        "connection": connection,
        "owned": True,
        "client": None,
        "namespace": _DEFAULT_NAMESPACE,
    }


def _close_resolved(resolved: dict[str, Any]) -> None:
    if not resolved.get("owned"):
        return
    client = resolved.get("client")
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
    connection = resolved.get("connection")
    if connection is not None:
        try:
            if connection.is_open:
                connection.close()
        except Exception:
            pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _decode_message(body: bytes | str | None) -> Any | None:
    if body is None:
        return None
    raw = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
    try:
        from dramatiq.message import Message

        return Message.decode(raw)
    except Exception:
        try:
            return json.loads(raw)
        except Exception:
            return None


def _message_timestamp_ms(message: Any) -> int | None:
    if message is None:
        return None
    if isinstance(message, dict):
        value = message.get("message_timestamp")
    else:
        value = getattr(message, "message_timestamp", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _message_eta_ms(message: Any) -> int | None:
    if message is None:
        return None
    if isinstance(message, dict):
        options = message.get("options") or {}
        value = options.get("eta") if isinstance(options, dict) else None
    else:
        options = getattr(message, "options", None) or {}
        value = options.get("eta") if isinstance(options, dict) else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _redis_job_queue_size(client: Any, queues: set[str], namespace: str) -> int:
    now_ms = _now_ms()
    total = 0
    for queue in queues:
        total += int(client.llen(f"{namespace}:{queue}") or 0)
        total += _redis_due_delayed_count(client, namespace, queue, now_ms)
    return total


def _redis_due_delayed_count(
    client: Any, namespace: str, queue: str, now_ms: int
) -> int:
    ids = client.lrange(f"{namespace}:{queue}.DQ", 0, -1)
    if not ids:
        return 0
    count = 0
    msgs_key = f"{namespace}:{queue}.DQ.msgs"
    for offset in range(0, len(ids), _HMGET_BATCH):
        batch = ids[offset : offset + _HMGET_BATCH]
        bodies = client.hmget(msgs_key, *batch)
        for body in bodies:
            message = _decode_message(body)
            eta = _message_eta_ms(message)
            if eta is not None and eta <= now_ms:
                count += 1
    return count


def _redis_job_queue_latency(client: Any, queues: set[str], namespace: str) -> float:
    now_ms = _now_ms()
    max_lat = 0.0
    for queue in queues:
        mid = client.lindex(f"{namespace}:{queue}", 0)
        if mid:
            body = client.hget(f"{namespace}:{queue}.msgs", mid)
            ts = _message_timestamp_ms(_decode_message(body))
            if ts is not None:
                max_lat = max(max_lat, max(0.0, (now_ms - ts) / 1000.0))

        earliest_due = _redis_earliest_due_eta(client, namespace, queue, now_ms)
        if earliest_due is not None:
            max_lat = max(max_lat, max(0.0, (now_ms - earliest_due) / 1000.0))
    return max_lat


def _redis_earliest_due_eta(
    client: Any, namespace: str, queue: str, now_ms: int
) -> int | None:
    ids = client.lrange(f"{namespace}:{queue}.DQ", 0, -1)
    if not ids:
        return None
    earliest: int | None = None
    msgs_key = f"{namespace}:{queue}.DQ.msgs"
    for offset in range(0, len(ids), _HMGET_BATCH):
        batch = ids[offset : offset + _HMGET_BATCH]
        bodies = client.hmget(msgs_key, *batch)
        for body in bodies:
            eta = _message_eta_ms(_decode_message(body))
            if eta is None or eta > now_ms:
                continue
            if earliest is None or eta < earliest:
                earliest = eta
    return earliest


def _rabbitmq_open_channel(connection: Any) -> Any:
    return connection.channel()


def _rabbitmq_job_queue_size(connection: Any, queues: set[str]) -> int:
    channel = _rabbitmq_open_channel(connection)
    total = 0
    for queue in queues:
        count, channel = _rabbitmq_ready_count(connection, channel, queue)
        total += count
    return total


def _rabbitmq_ready_count(connection: Any, channel: Any, queue: str) -> tuple[int, Any]:
    if channel is None or getattr(channel, "is_closed", False):
        channel = _rabbitmq_open_channel(connection)
    try:
        result = channel.queue_declare(queue=queue, passive=True)
        return int(result.method.message_count), channel
    except (AMQPChannelError, Exception) as error:
        if (
            isinstance(error, AMQPChannelError)
            or "404" in str(error)
            or "NOT_FOUND" in str(error)
            or getattr(channel, "is_closed", False)
        ):
            try:
                channel = _rabbitmq_open_channel(connection)
            except Exception:
                channel = None
            return 0, channel
        raise


def _rabbitmq_job_queue_latency(connection: Any, queues: set[str]) -> float:
    channel = _rabbitmq_open_channel(connection)
    now_ms = _now_ms()
    max_lat = 0.0
    for queue in queues:
        latency, channel = _rabbitmq_queue_latency(connection, channel, queue, now_ms)
        max_lat = max(max_lat, latency)
    return max_lat


def _rabbitmq_queue_latency(
    connection: Any, channel: Any, queue: str, now_ms: int
) -> tuple[float, Any]:
    if channel is None or getattr(channel, "is_closed", False):
        channel = _rabbitmq_open_channel(connection)
    try:
        method, _properties, body = channel.basic_get(queue, auto_ack=False)
    except (AMQPChannelError, Exception) as error:
        if (
            isinstance(error, AMQPChannelError)
            or "404" in str(error)
            or "NOT_FOUND" in str(error)
            or getattr(channel, "is_closed", False)
        ):
            try:
                channel = _rabbitmq_open_channel(connection)
            except Exception:
                channel = None
            return 0.0, channel
        raise

    if method is None:
        return 0.0, channel

    latency = 0.0
    try:
        ts = _message_timestamp_ms(_decode_message(body))
        if ts is not None:
            latency = max(0.0, (now_ms - ts) / 1000.0)
    finally:
        try:
            channel.basic_reject(method.delivery_tag, requeue=True)
        except Exception:
            pass
    return latency, channel
