import os
import time
import uuid
from unittest.mock import MagicMock

import pytest
import redis
from dramatiq import Message
from dramatiq.brokers.redis import RedisBroker

from hirefire_resource import plan
from hirefire_resource.errors import MissingQueueError
from hirefire_resource.macro import dramatiq as dramatiq_macro
from hirefire_resource.macro.dramatiq import (
    async_job_queue_latency,
    async_job_queue_size,
    job_queue_latency,
    job_queue_size,
)

redis_url = f"redis://127.0.0.1:{os.environ.get('REDIS_PORT', '6379')}/0"
amqp_url = f"amqp://guest:guest@127.0.0.1:{os.environ.get('RABBITMQ_PORT', '5672')}"
NAMESPACE = "dramatiq"

_ENV_LADDER_KEYS = (
    "AMQP_URL",
    "RABBITMQ_URL",
    "RABBITMQ_BIGWIG_URL",
    "CLOUDAMQP_URL",
    "REDIS_TLS_URL",
    "REDIS_URL",
    "REDISTOGO_URL",
    "REDISCLOUD_URL",
    "OPENREDIS_URL",
)


def _clear_broker_env(monkeypatch):
    for key in _ENV_LADDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_URL", raising=False)
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_NAMESPACE", raising=False)


@pytest.fixture(autouse=True)
def clear_redis():
    client = redis.Redis.from_url(redis_url)
    try:
        client.flushdb()
    finally:
        client.close()


def _encode(
    *,
    queue: str = "default",
    actor: str = "do_work",
    args: tuple = (),
    options: dict | None = None,
    message_timestamp: int | None = None,
) -> bytes:
    ts = message_timestamp if message_timestamp is not None else int(time.time() * 1000)
    msg = Message(
        queue_name=queue,
        actor_name=actor,
        args=args,
        kwargs={},
        options=options or {},
        message_id=str(uuid.uuid4()),
        message_timestamp=ts,
    )
    return msg.encode()


def _seed_live(
    client: redis.Redis,
    queue: str,
    *,
    age_s: float = 0,
    namespace: str = NAMESPACE,
    count: int = 1,
) -> None:
    now_ms = int(time.time() * 1000)
    ts = now_ms - int(age_s * 1000)
    for i in range(count):
        mid = str(uuid.uuid4())
        body = _encode(
            queue=queue,
            args=(i,),
            options={"redis_message_id": mid},
            message_timestamp=ts,
        )
        client.rpush(f"{namespace}:{queue}", mid)
        client.hset(f"{namespace}:{queue}.msgs", mid, body)


def _seed_delayed(
    client: redis.Redis,
    queue: str,
    *,
    eta_offset_s: float,
    age_s: float = 0,
    namespace: str = NAMESPACE,
) -> str:
    now_ms = int(time.time() * 1000)
    mid = str(uuid.uuid4())
    eta = now_ms + int(eta_offset_s * 1000)
    ts = now_ms - int(age_s * 1000)
    body = _encode(
        queue=queue,
        options={"redis_message_id": mid, "eta": eta},
        message_timestamp=ts,
    )
    client.rpush(f"{namespace}:{queue}.DQ", mid)
    client.hset(f"{namespace}:{queue}.DQ.msgs", mid, body)
    return mid


def _seed_working(
    client: redis.Redis,
    queue: str,
    *,
    namespace: str = NAMESPACE,
) -> None:
    """Simulate fetched-not-acked work: id only in acks set, body remains in msgs."""
    mid = str(uuid.uuid4())
    body = _encode(queue=queue, options={"redis_message_id": mid})
    client.hset(f"{namespace}:{queue}.msgs", mid, body)
    client.sadd(f"{namespace}:__acks__.worker-1.{queue}", mid)


def _seed_xq(client: redis.Redis, queue: str, *, namespace: str = NAMESPACE) -> None:
    mid = str(uuid.uuid4())
    body = _encode(queue=queue, options={"redis_message_id": mid})
    client.zadd(f"{namespace}:{queue}.XQ", {mid: time.time() * 1000})
    client.hset(f"{namespace}:{queue}.XQ.msgs", mid, body)


def test_library_loaded_is_true_when_dramatiq_package_is_imported():
    assert plan.library_loaded("dramatiq")
    assert plan.executable("dramatiq")
    assert plan.any_allowlisted_job_queue_library_loaded()


def test_job_queue_size_requires_queues():
    with pytest.raises(MissingQueueError):
        job_queue_size(broker_url=redis_url)


def test_job_queue_size_empty_queue():
    assert job_queue_size("default", broker_url=redis_url) == 0


def test_job_queue_size_live_only():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2)
        _seed_live(client, "mailer", count=1)
        assert job_queue_size("default", broker_url=redis_url) == 2
        assert job_queue_size("mailer", broker_url=redis_url) == 1
        assert job_queue_size("default", "mailer", broker_url=redis_url) == 3
    finally:
        client.close()


def test_job_queue_size_due_delayed_counted_future_excluded():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2)
        _seed_delayed(client, "default", eta_offset_s=-30)
        _seed_delayed(client, "default", eta_offset_s=60)
        assert client.llen(f"{NAMESPACE}:default") == 2
        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
        assert job_queue_size("default", broker_url=redis_url) == 3
        assert client.llen(f"{NAMESPACE}:default") == 2
        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
    finally:
        client.close()


def test_job_queue_size_due_only():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_delayed(client, "default", eta_offset_s=-20)
        _seed_delayed(client, "default", eta_offset_s=-5)
        assert client.llen(f"{NAMESPACE}:default") == 0
        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
        assert job_queue_size("default", broker_url=redis_url) == 2
    finally:
        client.close()


def test_job_queue_size_future_only_excluded():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_delayed(client, "default", eta_offset_s=120)
        _seed_delayed(client, "default", eta_offset_s=300)
        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
        assert job_queue_size("default", broker_url=redis_url) == 0
    finally:
        client.close()


def test_job_queue_size_working_and_xq_excluded():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_working(client, "default")
        _seed_xq(client, "default")
        assert client.llen(f"{NAMESPACE}:default") == 0
        assert client.scard(f"{NAMESPACE}:__acks__.worker-1.default") == 1
        assert client.zcard(f"{NAMESPACE}:default.XQ") == 1
        assert job_queue_size("default", broker_url=redis_url) == 0
        _seed_live(client, "default", count=1)
        assert client.llen(f"{NAMESPACE}:default") == 1
        assert job_queue_size("default", broker_url=redis_url) == 1
    finally:
        client.close()


def test_job_queue_size_namespace():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2, namespace="app")
        assert job_queue_size("default", broker_url=redis_url, namespace="app") == 2
        assert job_queue_size("default", broker_url=redis_url) == 0
    finally:
        client.close()


def test_job_queue_size_dedupes_and_canonicalizes_dq_suffix():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2)
        assert (
            job_queue_size(
                "default", " default ", "default", "default.DQ", broker_url=redis_url
            )
            == 2
        )
    finally:
        client.close()


def test_job_queue_size_decode_responses_url():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=1)
        decode_url = (
            redis_url + ("&" if "?" in redis_url else "?") + "decode_responses=true"
        )
        assert job_queue_size("default", broker_url=decode_url) == 1
    finally:
        client.close()


def test_job_queue_size_real_redis_broker_enqueue():
    broker = RedisBroker(url=redis_url, namespace=NAMESPACE)
    try:
        broker.declare_queue("default")
        msg = Message(
            queue_name="default",
            actor_name="do_work",
            args=(1,),
            kwargs={},
            options={},
        )
        broker.enqueue(msg)
        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_size("default", broker=broker) == 1
    finally:
        broker.client.flushdb()
        broker.close()


def test_job_queue_size_broker_xor_broker_url():
    broker = RedisBroker(url=redis_url, namespace=NAMESPACE)
    try:
        with pytest.raises(ValueError, match="both 'broker' and 'broker_url'"):
            job_queue_size("default", broker=broker, broker_url=redis_url)
    finally:
        broker.close()


def test_job_queue_size_broker_injection_does_not_close_client():
    broker = RedisBroker(url=redis_url, namespace=NAMESPACE)
    try:
        broker.declare_queue("default")
        broker.enqueue(
            Message(
                queue_name="default",
                actor_name="do_work",
                args=(),
                kwargs={},
                options={},
            )
        )
        assert job_queue_size("default", broker=broker) == 1
        assert broker.client.llen(f"{NAMESPACE}:default") == 1
    finally:
        broker.client.flushdb()
        broker.close()


def test_job_queue_size_url_owned_client_closed(monkeypatch):
    closed = {"n": 0}
    real_from_url = redis.Redis.from_url

    def tracking_from_url(url, **kwargs):
        client = real_from_url(url, **kwargs)
        original_close = client.close

        def close():
            closed["n"] += 1
            return original_close()

        client.close = close  # type: ignore[method-assign]
        return client

    monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)
    assert job_queue_size("default", broker_url=redis_url) == 0
    assert closed["n"] == 1


def test_job_queue_latency_empty():
    assert job_queue_latency("default", broker_url=redis_url) == 0


def test_job_queue_latency_live_head_age():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", age_s=200)
        _seed_live(client, "mailer", age_s=50)
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            200, abs=5
        )
        assert job_queue_latency("mailer", broker_url=redis_url) == pytest.approx(
            50, abs=5
        )
        assert job_queue_latency(
            "default", "mailer", broker_url=redis_url
        ) == pytest.approx(200, abs=5)
    finally:
        client.close()


def test_job_queue_latency_uses_lindex_zero_not_max_ready_age():
    """Live JQL is LINDEX 0 only (FIFO head), not max age across the ready list.

    Seed younger first, then older (RPUSH). Head is young. A wrong scan of all
    ready bodies for max(message_timestamp age) would report the older tail.
    """
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", age_s=25, count=1)
        _seed_live(client, "default", age_s=400, count=1)
        assert client.llen(f"{NAMESPACE}:default") == 2
        head_id = client.lindex(f"{NAMESPACE}:default", 0)
        tail_id = client.lindex(f"{NAMESPACE}:default", -1)
        assert head_id != tail_id
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            25, abs=5
        )
        tail_body = client.hget(f"{NAMESPACE}:default.msgs", tail_id)
        tail_msg = Message.decode(tail_body)
        now_ms = int(time.time() * 1000)
        tail_age = (now_ms - int(tail_msg.message_timestamp)) / 1000.0
        assert tail_age == pytest.approx(400, abs=5)
    finally:
        client.close()


def test_job_queue_latency_due_delayed_lateness():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_delayed(client, "default", eta_offset_s=-150)
        _seed_delayed(client, "default", eta_offset_s=-450)
        _seed_delayed(client, "default", eta_offset_s=120)
        assert client.llen(f"{NAMESPACE}:default.DQ") == 3
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            450, abs=5
        )
    finally:
        client.close()


def test_job_queue_latency_future_only_is_zero():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_delayed(client, "default", eta_offset_s=90)
        _seed_delayed(client, "default", eta_offset_s=300)
        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
        assert job_queue_latency("default", broker_url=redis_url) == 0
    finally:
        client.close()


def test_job_queue_latency_max_of_live_and_due():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", age_s=80)
        _seed_delayed(client, "default", eta_offset_s=-300)
        assert client.llen(f"{NAMESPACE}:default") == 1
        assert client.llen(f"{NAMESPACE}:default.DQ") == 1
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            300, abs=5
        )
        client.flushdb()
        _seed_live(client, "default", age_s=500)
        _seed_delayed(client, "default", eta_offset_s=-40)
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            500, abs=5
        )
    finally:
        client.close()


def test_job_queue_latency_working_and_xq_excluded():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_working(client, "default")
        _seed_xq(client, "default")
        assert job_queue_latency("default", broker_url=redis_url) == 0
    finally:
        client.close()


def test_job_queue_latency_corrupt_head_does_not_hide_sibling():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", age_s=180)
        mid = str(uuid.uuid4())
        client.rpush(f"{NAMESPACE}:broken", mid)
        client.hset(f"{NAMESPACE}:broken.msgs", mid, b"not-json-{{{")
        assert job_queue_latency(
            "default", "broken", broker_url=redis_url
        ) == pytest.approx(180, abs=5)
        assert job_queue_size("default", "broken", broker_url=redis_url) == 2
    finally:
        client.close()


def test_job_queue_latency_corrupt_live_head_is_zero_size_still_counts():
    client = redis.Redis.from_url(redis_url)
    try:
        mid = str(uuid.uuid4())
        client.rpush(f"{NAMESPACE}:default", mid)
        client.hset(f"{NAMESPACE}:default.msgs", mid, b"not-json-{{{")
        assert client.llen(f"{NAMESPACE}:default") == 1
        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_latency("default", broker_url=redis_url) == 0
    finally:
        client.close()


def test_job_queue_size_eta_equal_to_now_is_due(monkeypatch):
    """Inclusive filter: options.eta <= now_ms (equality is due, not future)."""
    fixed_now = 1_722_600_000_000
    monkeypatch.setattr(dramatiq_macro, "_now_ms", lambda: fixed_now)

    client = redis.Redis.from_url(redis_url)
    try:
        mid = str(uuid.uuid4())
        body = _encode(
            queue="default",
            options={"redis_message_id": mid, "eta": fixed_now},
            message_timestamp=fixed_now - 5_000,
        )
        client.rpush(f"{NAMESPACE}:default.DQ", mid)
        client.hset(f"{NAMESPACE}:default.DQ.msgs", mid, body)
        mid_future = str(uuid.uuid4())
        body_future = _encode(
            queue="default",
            options={"redis_message_id": mid_future, "eta": fixed_now + 1},
            message_timestamp=fixed_now,
        )
        client.rpush(f"{NAMESPACE}:default.DQ", mid_future)
        client.hset(f"{NAMESPACE}:default.DQ.msgs", mid_future, body_future)

        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_latency("default", broker_url=redis_url) == 0.0
    finally:
        client.close()


def test_job_queue_size_missing_eta_on_dq_skipped():
    """Defensive: DQ body without options.eta is not treated as due."""
    client = redis.Redis.from_url(redis_url)
    try:
        mid = str(uuid.uuid4())
        body = _encode(
            queue="default",
            options={"redis_message_id": mid},
            message_timestamp=int(time.time() * 1000) - 60_000,
        )
        client.rpush(f"{NAMESPACE}:default.DQ", mid)
        client.hset(f"{NAMESPACE}:default.DQ.msgs", mid, body)
        assert client.llen(f"{NAMESPACE}:default.DQ") == 1
        assert job_queue_size("default", broker_url=redis_url) == 0
        assert job_queue_latency("default", broker_url=redis_url) == 0
    finally:
        client.close()


def test_job_queue_size_corrupt_dq_body_skipped():
    client = redis.Redis.from_url(redis_url)
    try:
        mid = str(uuid.uuid4())
        client.rpush(f"{NAMESPACE}:default.DQ", mid)
        client.hset(f"{NAMESPACE}:default.DQ.msgs", mid, b"not-json-{{{")
        _seed_live(client, "default", count=1)
        assert client.llen(f"{NAMESPACE}:default") == 1
        assert client.llen(f"{NAMESPACE}:default.DQ") == 1
        assert job_queue_size("default", broker_url=redis_url) == 1
    finally:
        client.close()


def test_job_queue_size_never_uses_do_qsize(monkeypatch):
    """HireFire must not call Dramatiq do_qsize (msgs+acks includes working)."""
    broker = RedisBroker(url=redis_url, namespace=NAMESPACE)
    try:
        broker.declare_queue("default")
        broker.enqueue(
            Message(
                queue_name="default",
                actor_name="do_work",
                args=(),
                kwargs={},
                options={},
            )
        )
        client = broker.client
        mid = str(uuid.uuid4())
        body = _encode(queue="default", options={"redis_message_id": mid})
        client.hset(f"{NAMESPACE}:default.msgs", mid, body)
        acks_key = f"{NAMESPACE}:__acks__.{broker.broker_id}.default"
        client.sadd(acks_key, mid)

        assert client.llen(f"{NAMESPACE}:default") == 1
        assert client.hlen(f"{NAMESPACE}:default.msgs") == 2
        assert client.scard(acks_key) == 1

        assert broker.do_qsize("default") == 3

        dispatch_commands: list[str] = []
        real_dispatch = broker._dispatch

        def tracking_dispatch(command):
            dispatch_commands.append(command)
            return real_dispatch(command)

        monkeypatch.setattr(broker, "_dispatch", tracking_dispatch)

        assert job_queue_size("default", broker=broker) == 1
        assert job_queue_latency("default", broker=broker) < 1.0
        assert "qsize" not in dispatch_commands
        eval_calls = {"n": 0}
        real_from_url = redis.Redis.from_url

        def tracking_from_url(url, **kwargs):
            sample = real_from_url(url, **kwargs)
            real_eval = sample.eval

            def eval_(*args, **kwargs):
                eval_calls["n"] += 1
                return real_eval(*args, **kwargs)

            sample.eval = eval_  # type: ignore[method-assign]
            return sample

        monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)
        assert job_queue_size("default", broker_url=redis_url) == 1
        assert eval_calls["n"] == 0
    finally:
        broker.client.flushdb()
        broker.close()


def test_job_queue_size_redis_command_keys_are_waiting_structures_only(monkeypatch):
    """Pin Redis key names: live list + DQ (+ msgs). Never XQ, acks, or do_qsize."""
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=1, age_s=30)
        _seed_delayed(client, "default", eta_offset_s=-15)
        _seed_delayed(client, "default", eta_offset_s=60)
        _seed_working(client, "default")
        _seed_xq(client, "default")

        def _as_str_key(key):
            if isinstance(key, bytes):
                return key.decode("utf-8")
            return str(key)

        recorded: list[str] = []
        forbidden_methods: list[str] = []
        real_from_url = redis.Redis.from_url

        def tracking_from_url(url, **kwargs):
            sample = real_from_url(url, **kwargs)
            for method_name in ("llen", "lrange", "lindex", "hget", "hmget"):
                real_method = getattr(sample, method_name)

                def make_wrapper(real=real_method):
                    def wrapper(key, *args, **kwargs):
                        recorded.append(_as_str_key(key))
                        return real(key, *args, **kwargs)

                    return wrapper

                setattr(sample, method_name, make_wrapper())

            for method_name in ("scard", "zcard", "eval"):
                real_method = getattr(sample, method_name)

                def make_forbidden(name=method_name, real=real_method):
                    def wrapper(*args, **kwargs):
                        forbidden_methods.append(name)
                        return real(*args, **kwargs)

                    return wrapper

                setattr(sample, method_name, make_forbidden())

            real_execute = sample.execute_command

            def execute_command(*args, **kwargs):
                cmd = args[0] if args else None
                if isinstance(cmd, bytes):
                    cmd = cmd.decode("utf-8")
                if isinstance(cmd, str) and cmd.upper() in {
                    "SCARD",
                    "ZCARD",
                    "EVAL",
                    "EVALSHA",
                }:
                    forbidden_methods.append(f"execute_command:{cmd.upper()}")
                return real_execute(*args, **kwargs)

            sample.execute_command = execute_command  # type: ignore[method-assign]
            return sample

        monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)

        assert job_queue_size("default", broker_url=redis_url) == 2
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            30, abs=5
        )

        assert any(k == f"{NAMESPACE}:default" for k in recorded)
        assert any(k == f"{NAMESPACE}:default.DQ" for k in recorded)
        assert any(k == f"{NAMESPACE}:default.msgs" for k in recorded)
        assert any(k == f"{NAMESPACE}:default.DQ.msgs" for k in recorded)
        assert not any(".XQ" in k for k in recorded)
        assert not any("__acks__" in k for k in recorded)
        assert forbidden_methods == []
    finally:
        client.close()


def test_job_queue_size_due_delayed_hmget_batching_across_boundary(monkeypatch):
    """Due scan must page HMGET across _HMGET_BATCH (not only the first page)."""
    batch = dramatiq_macro._HMGET_BATCH
    assert batch >= 2
    client = redis.Redis.from_url(redis_url)
    try:
        for _ in range(batch):
            _seed_delayed(client, "default", eta_offset_s=120)
        _seed_delayed(client, "default", eta_offset_s=-40)
        assert client.llen(f"{NAMESPACE}:default.DQ") == batch + 1

        hmget_calls: list[int] = []
        real_from_url = redis.Redis.from_url

        def tracking_from_url(url, **kwargs):
            sample = real_from_url(url, **kwargs)
            real_hmget = sample.hmget

            def hmget(key, *fields, **kw):
                hmget_calls.append(len(fields))
                return real_hmget(key, *fields, **kw)

            sample.hmget = hmget  # type: ignore[method-assign]
            return sample

        monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)

        assert job_queue_size("default", broker_url=redis_url) == 1
        assert len(hmget_calls) >= 2
        assert hmget_calls[0] == batch
        assert sum(hmget_calls) >= batch + 1
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            40, abs=5
        )
    finally:
        client.close()


def test_job_queue_size_caps_dq_walk_and_omits_due_past_the_limit(monkeypatch):
    import json

    limit = dramatiq_macro._DQ_WALK_LIMIT
    client = redis.Redis.from_url(redis_url)
    try:
        now_ms = int(time.time() * 1000)
        future = now_ms + 3_600_000
        due = now_ms - 40_000
        pipe = client.pipeline()
        ids = []
        for i in range(limit):
            mid = f"future{i}"
            ids.append(mid)
            pipe.hset(
                f"{NAMESPACE}:default.DQ.msgs",
                mid,
                json.dumps({"options": {"eta": future}, "message_timestamp": now_ms}),
            )
        pipe.rpush(f"{NAMESPACE}:default.DQ", *ids)
        pipe.hset(
            f"{NAMESPACE}:default.DQ.msgs",
            "due-past-cap",
            json.dumps({"options": {"eta": due}, "message_timestamp": now_ms}),
        )
        pipe.rpush(f"{NAMESPACE}:default.DQ", "due-past-cap")
        pipe.execute()

        ranges: list[tuple[int, int]] = []
        real_from_url = redis.Redis.from_url

        def tracking_from_url(url, **kwargs):
            sample = real_from_url(url, **kwargs)
            real_lrange = sample.lrange

            def lrange(key, start, end):
                ranges.append((start, end))
                return real_lrange(key, start, end)

            sample.lrange = lrange  # type: ignore[method-assign]
            return sample

        monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)
        assert job_queue_size("default", broker_url=redis_url) == 0
        assert ranges == [(0, limit - 1)]
    finally:
        client.close()


def test_dq_wave_cache_does_not_reuse_stats_across_broker_urls():
    url0 = redis_url
    url1 = redis_url.rsplit("/", 1)[0] + "/1"
    client0 = redis.Redis.from_url(url0)
    client1 = redis.Redis.from_url(url1)
    try:
        client0.flushdb()
        client1.flushdb()
        _seed_delayed(client0, "default", eta_offset_s=-10)
        _seed_delayed(client1, "default", eta_offset_s=-10)
        _seed_delayed(client1, "default", eta_offset_s=-20)
        dramatiq_macro.before_sample_job_queues()
        try:
            assert job_queue_size("default", broker_url=url0) == 1
            assert job_queue_size("default", broker_url=url1) == 2
        finally:
            dramatiq_macro.after_sample_job_queues()
    finally:
        client0.flushdb()
        client1.flushdb()
        client0.close()
        client1.close()


def test_dq_walk_is_memoized_for_one_sample_wave(monkeypatch):
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_delayed(client, "default", eta_offset_s=-40)
        lrange_calls = [0]
        real_from_url = redis.Redis.from_url

        def tracking_from_url(url, **kwargs):
            sample = real_from_url(url, **kwargs)
            real_lrange = sample.lrange

            def lrange(key, start, end):
                lrange_calls[0] += 1
                return real_lrange(key, start, end)

            sample.lrange = lrange  # type: ignore[method-assign]
            return sample

        monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)
        dramatiq_macro.before_sample_job_queues()
        try:
            assert job_queue_size("default", broker_url=redis_url) == 1
            assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
                40, abs=5
            )
            assert lrange_calls[0] == 1
        finally:
            dramatiq_macro.after_sample_job_queues()

        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            40, abs=5
        )
        assert lrange_calls[0] == 3
    finally:
        client.close()


def test_sample_redis_sets_socket_timeouts(monkeypatch):
    seen: dict[str, object] = {}
    real_from_url = redis.Redis.from_url

    def tracking_from_url(url, **kwargs):
        seen.update(kwargs)
        return real_from_url(url, **kwargs)

    monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)
    assert job_queue_size("default", broker_url=redis_url) == 0
    assert seen["socket_timeout"] == dramatiq_macro._SAMPLE_REDIS_TIMEOUT
    assert seen["socket_connect_timeout"] == dramatiq_macro._SAMPLE_REDIS_TIMEOUT


def test_job_queue_size_multi_queue_due_and_live_unequal():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2)
        _seed_delayed(client, "default", eta_offset_s=-10)
        _seed_delayed(client, "default", eta_offset_s=50)
        _seed_live(client, "mailer", count=1)
        _seed_delayed(client, "mailer", eta_offset_s=-5)
        _seed_delayed(client, "mailer", eta_offset_s=-8)
        assert client.llen(f"{NAMESPACE}:default") == 2
        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
        assert client.llen(f"{NAMESPACE}:mailer") == 1
        assert client.llen(f"{NAMESPACE}:mailer.DQ") == 2
        assert job_queue_size("default", broker_url=redis_url) == 3
        assert job_queue_size("mailer", broker_url=redis_url) == 3
        assert job_queue_size("default", "mailer", broker_url=redis_url) == 6
    finally:
        client.close()


def test_job_queue_size_queue_name_with_dots():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "email.priority", count=2)
        _seed_delayed(client, "email.priority", eta_offset_s=-12)
        assert client.llen(f"{NAMESPACE}:email.priority") == 2
        assert client.llen(f"{NAMESPACE}:email.priority.DQ") == 1
        assert job_queue_size("email.priority", broker_url=redis_url) == 3
        assert job_queue_size("email", broker_url=redis_url) == 0
    finally:
        client.close()


def test_job_queue_size_canonicalizes_xq_suffix():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2)
        _seed_xq(client, "default")
        assert job_queue_size("default.XQ", "default.XQ", broker_url=redis_url) == 2
        assert client.zcard(f"{NAMESPACE}:default.XQ") == 1
    finally:
        client.close()


def test_job_queue_size_invalid_eta_type_skipped():
    client = redis.Redis.from_url(redis_url)
    try:
        mid = str(uuid.uuid4())
        body = _encode(
            queue="default",
            options={"redis_message_id": mid, "eta": "not-a-number"},
            message_timestamp=int(time.time() * 1000) - 60_000,
        )
        client.rpush(f"{NAMESPACE}:default.DQ", mid)
        client.hset(f"{NAMESPACE}:default.DQ.msgs", mid, body)
        _seed_live(client, "default", count=1)
        assert client.llen(f"{NAMESPACE}:default.DQ") == 1
        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_latency("default", broker_url=redis_url) < 1.0
    finally:
        client.close()


def test_job_queue_latency_missing_or_future_message_timestamp_is_zero(monkeypatch):
    """Missing timestamp → no invented age. Future timestamp clamps to 0."""
    import json

    fixed_now = 1_722_600_000_000
    monkeypatch.setattr(dramatiq_macro, "_now_ms", lambda: fixed_now)
    client = redis.Redis.from_url(redis_url)
    try:
        mid_missing = str(uuid.uuid4())
        body_missing = json.dumps(
            {
                "queue_name": "default",
                "actor_name": "do_work",
                "args": [],
                "kwargs": {},
                "options": {"redis_message_id": mid_missing},
                "message_id": mid_missing,
                "message_timestamp": None,
            }
        ).encode("utf-8")
        decoded = Message.decode(body_missing)
        assert decoded.message_timestamp is None
        client.rpush(f"{NAMESPACE}:default", mid_missing)
        client.hset(f"{NAMESPACE}:default.msgs", mid_missing, body_missing)
        assert client.llen(f"{NAMESPACE}:default") == 1
        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_latency("default", broker_url=redis_url) == 0.0

        client.flushdb()
        mid_future = str(uuid.uuid4())
        body_future = _encode(
            queue="default",
            options={"redis_message_id": mid_future},
            message_timestamp=fixed_now + 60_000,
        )
        client.rpush(f"{NAMESPACE}:default", mid_future)
        client.hset(f"{NAMESPACE}:default.msgs", mid_future, body_future)
        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_latency("default", broker_url=redis_url) == 0.0
    finally:
        client.close()


def test_job_queue_size_json_fallback_when_message_decode_fails(monkeypatch):
    """Decode falls back to json.loads when dramatiq.Message.decode raises."""
    import json

    client = redis.Redis.from_url(redis_url)
    try:
        fixed_now = 1_722_600_000_000
        monkeypatch.setattr(dramatiq_macro, "_now_ms", lambda: fixed_now)
        mid = str(uuid.uuid4())
        payload = json.dumps(
            {
                "queue_name": "default",
                "actor_name": "do_work",
                "args": [],
                "kwargs": {},
                "options": {"redis_message_id": mid, "eta": fixed_now - 10_000},
                "message_id": mid,
                "message_timestamp": fixed_now - 20_000,
            }
        ).encode("utf-8")
        client.rpush(f"{NAMESPACE}:default.DQ", mid)
        client.hset(f"{NAMESPACE}:default.DQ.msgs", mid, payload)

        def boom_decode(raw):
            raise RuntimeError("decode boom")

        monkeypatch.setattr(Message, "decode", staticmethod(boom_decode))

        assert job_queue_size("default", broker_url=redis_url) == 1
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            10.0, abs=0.01
        )
    finally:
        client.close()


def test_job_queue_size_waiting_only_mixed_structure():
    """Live + due only when future, acks, and XQ are also present."""
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2, age_s=40)
        _seed_delayed(client, "default", eta_offset_s=-25)
        _seed_delayed(client, "default", eta_offset_s=90)
        _seed_working(client, "default")
        _seed_xq(client, "default")

        assert client.llen(f"{NAMESPACE}:default") == 2
        assert client.llen(f"{NAMESPACE}:default.DQ") == 2
        assert client.scard(f"{NAMESPACE}:__acks__.worker-1.default") == 1
        assert client.zcard(f"{NAMESPACE}:default.XQ") == 1

        assert job_queue_size("default", broker_url=redis_url) == 3
        assert job_queue_latency("default", broker_url=redis_url) == pytest.approx(
            40, abs=5
        )
    finally:
        client.close()


def test_job_queue_size_uses_injected_broker_namespace():
    broker = RedisBroker(url=redis_url, namespace="appns")
    try:
        _seed_live(broker.client, "default", count=2, namespace="appns")
        assert job_queue_size("default", broker=broker) == 2
        assert job_queue_size("default", broker_url=redis_url) == 0
    finally:
        broker.client.flushdb()
        broker.close()


def test_job_queue_size_explicit_namespace_overrides_broker_namespace():
    broker = RedisBroker(url=redis_url, namespace="appns")
    try:
        _seed_live(broker.client, "default", count=2, namespace="override")
        assert job_queue_size("default", broker=broker, namespace="override") == 2
        assert job_queue_size("default", broker=broker) == 0
    finally:
        broker.client.flushdb()
        broker.close()


def test_job_queue_size_env_namespace_with_broker_url(monkeypatch):
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "envns")
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", count=2, namespace="envns")
        assert job_queue_size("default", broker_url=redis_url) == 2
        assert job_queue_size("default", broker_url=redis_url, namespace="other") == 0
    finally:
        client.close()


def test_job_queue_size_real_redis_broker_future_delay_excluded():
    broker = RedisBroker(url=redis_url, namespace=NAMESPACE)
    try:
        broker.declare_queue("default")
        broker.enqueue(
            Message(
                queue_name="default",
                actor_name="do_work",
                args=(1,),
                kwargs={},
                options={},
            )
        )
        broker.enqueue(
            Message(
                queue_name="default",
                actor_name="do_work",
                args=(2,),
                kwargs={},
                options={},
            ),
            delay=120_000,
        )
        assert broker.client.llen(f"{NAMESPACE}:default") == 1
        assert broker.client.llen(f"{NAMESPACE}:default.DQ") == 1
        assert job_queue_size("default", broker=broker) == 1
        assert job_queue_size("default", broker_url=redis_url) == 1
    finally:
        broker.client.flushdb()
        broker.close()


def test_job_queue_latency_requires_queues():
    with pytest.raises(MissingQueueError):
        job_queue_latency(broker_url=redis_url)


def test_job_queue_size_blank_queue_names_raise():
    with pytest.raises(MissingQueueError):
        job_queue_size("  ", "", broker_url=redis_url)


def test_redis_broker_missing_client_raises():
    FakeRedisBroker = type(
        "RedisBroker",
        (),
        {
            "__module__": "dramatiq.brokers.redis",
            "client": None,
            "namespace": "dramatiq",
        },
    )
    with pytest.raises(ValueError, match="no client"):
        job_queue_size("default", broker=FakeRedisBroker())


@pytest.mark.asyncio
async def test_async_job_queue_size_and_latency():
    client = redis.Redis.from_url(redis_url)
    try:
        _seed_live(client, "default", age_s=100, count=2)
        assert await async_job_queue_size("default", broker_url=redis_url) == 2
        assert await async_job_queue_latency(
            "default", broker_url=redis_url
        ) == pytest.approx(100, abs=5)
    finally:
        client.close()


@pytest.mark.asyncio
async def test_async_job_queue_size_requires_queues():
    with pytest.raises(MissingQueueError):
        await async_job_queue_size(broker_url=redis_url)


@pytest.mark.asyncio
async def test_async_broker_xor_broker_url():
    broker = RedisBroker(url=redis_url, namespace=NAMESPACE)
    try:
        with pytest.raises(ValueError, match="both 'broker' and 'broker_url'"):
            await async_job_queue_size("default", broker=broker, broker_url=redis_url)
        with pytest.raises(ValueError, match="both 'broker' and 'broker_url'"):
            await async_job_queue_latency(
                "default", broker=broker, broker_url=redis_url
            )
    finally:
        broker.close()


def test_resolve_broker_url_multi_key_precedence(monkeypatch):
    """Full ladder: first set key wins. AMQP family before Redis family."""
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("OPENREDIS_URL", "redis://openredis/0")
    monkeypatch.setenv("REDISCLOUD_URL", "redis://rediscloud/0")
    monkeypatch.setenv("REDISTOGO_URL", "redis://redistogo/0")
    monkeypatch.setenv("REDIS_URL", "redis://redis-url/0")
    monkeypatch.setenv("REDIS_TLS_URL", "rediss://redis-tls/0")
    monkeypatch.setenv("CLOUDAMQP_URL", "amqp://cloudamqp")
    monkeypatch.setenv("RABBITMQ_BIGWIG_URL", "amqp://bigwig")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://rabbitmq-url")
    monkeypatch.setenv("AMQP_URL", "amqp://amqp-url")
    assert dramatiq_macro._resolve_broker_url(None) == "amqp://amqp-url"

    monkeypatch.delenv("AMQP_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "amqp://rabbitmq-url"

    monkeypatch.delenv("RABBITMQ_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "amqp://bigwig"

    monkeypatch.delenv("RABBITMQ_BIGWIG_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "amqp://cloudamqp"

    monkeypatch.delenv("CLOUDAMQP_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "rediss://redis-tls/0"

    monkeypatch.delenv("REDIS_TLS_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "redis://redis-url/0"

    monkeypatch.delenv("REDIS_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "redis://redistogo/0"

    monkeypatch.delenv("REDISTOGO_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "redis://rediscloud/0"

    monkeypatch.delenv("REDISCLOUD_URL")
    assert dramatiq_macro._resolve_broker_url(None) == "redis://openredis/0"


def test_resolve_broker_url_explicit_wins_over_env(monkeypatch):
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("AMQP_URL", "amqp://from-env")
    monkeypatch.setenv("REDIS_URL", "redis://from-env/0")
    assert (
        dramatiq_macro._resolve_broker_url("redis://explicit/1") == "redis://explicit/1"
    )


def test_hirefire_dramatiq_url_is_plan_only_not_macro_ladder(monkeypatch):
    """HIREFIRE_DRAMATIQ_URL is applied via plan_connection_options, not _resolve_broker_url."""
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "redis://hirefire-override/0")
    monkeypatch.setenv("REDIS_URL", "redis://platform/0")
    assert dramatiq_macro._resolve_broker_url(None) == "redis://platform/0"
    assert dramatiq_macro.plan_connection_options() == {
        "broker_url": "redis://hirefire-override/0"
    }


def test_url_owned_client_closed_after_sampling_error(monkeypatch):
    closed = {"n": 0}
    real_from_url = redis.Redis.from_url

    def tracking_from_url(url, **kwargs):
        client = real_from_url(url, **kwargs)
        original_close = client.close
        original_llen = client.llen

        def llen(key):
            raise redis.exceptions.RedisError("forced mid-sample failure")

        def close():
            closed["n"] += 1
            return original_close()

        client.llen = llen  # type: ignore[method-assign]
        client.close = close  # type: ignore[method-assign]
        assert original_llen is not None
        return client

    monkeypatch.setattr(dramatiq_macro.redis.Redis, "from_url", tracking_from_url)
    assert job_queue_size("default", broker_url=redis_url) == 0
    assert closed["n"] == 1


def test_unreachable_redis_returns_zero():
    assert job_queue_size("default", broker_url="redis://127.0.0.1:1/0") == 0
    assert job_queue_latency("default", broker_url="redis://127.0.0.1:1/0") == 0


def test_unsupported_broker_type_raises():
    with pytest.raises(ValueError, match="Unsupported Dramatiq broker"):
        job_queue_size("default", broker=MagicMock())


def test_url_kind_detects_amqp_and_redis_schemes():
    assert dramatiq_macro._url_kind("amqp://guest:guest@localhost/") == "rabbitmq"
    assert dramatiq_macro._url_kind("amqps://guest:guest@localhost/") == "rabbitmq"
    assert dramatiq_macro._url_kind("redis://localhost:6379/0") == "redis"
    assert dramatiq_macro._url_kind("rediss://localhost:6379/0") == "redis"


def test_resolve_namespace_precedence(monkeypatch):
    _clear_broker_env(monkeypatch)
    broker = MagicMock()
    broker.namespace = "from-broker"
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "from-env")
    assert (
        dramatiq_macro._resolve_namespace("explicit", broker, redis_path=True)
        == "explicit"
    )
    assert (
        dramatiq_macro._resolve_namespace(None, broker, redis_path=True)
        == "from-broker"
    )
    assert dramatiq_macro._resolve_namespace(None, None, redis_path=True) == "from-env"
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_NAMESPACE")
    assert dramatiq_macro._resolve_namespace(None, None, redis_path=True) == "dramatiq"


def test_resolve_broker_url_default_prefers_amqp_when_pika_available(monkeypatch):
    _clear_broker_env(monkeypatch)
    if dramatiq_macro.PIKA_AVAILABLE:
        assert (
            dramatiq_macro._resolve_broker_url(None)
            == "amqp://guest:guest@localhost:5672"
        )
    else:
        assert dramatiq_macro._resolve_broker_url(None) == "redis://localhost:6379/0"


def test_url_kind_amqp_scheme_fragment():
    assert dramatiq_macro._url_kind("amqp+ssl://guest@host/") == "rabbitmq"


def test_rabbitmq_broker_missing_parameters_raises():
    FakeRabbit = type(
        "RabbitmqBroker",
        (),
        {"__module__": "dramatiq.brokers.rabbitmq", "parameters": None},
    )
    with pytest.raises(ValueError, match="connection parameters"):
        job_queue_size("default", broker=FakeRabbit())


pika = pytest.importorskip("pika")


def _rmq_cleanup(queue: str) -> None:
    connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
    try:
        channel = connection.channel()
        for name in (queue, f"{queue}.DQ", f"{queue}.XQ"):
            try:
                channel.queue_delete(name)
            except Exception:
                channel = connection.channel()
    finally:
        if connection.is_open:
            connection.close()


def test_rabbitmq_job_queue_size_main_ready_only():
    queue = f"hf_dramatiq_size_{uuid.uuid4().hex[:8]}"
    _rmq_cleanup(queue)
    try:
        from dramatiq.brokers.rabbitmq import RabbitmqBroker

        broker = RabbitmqBroker(url=amqp_url, confirm_delivery=True)
        try:
            broker.declare_queue(queue)
            for _ in range(2):
                broker.enqueue(
                    Message(
                        queue_name=queue,
                        actor_name="do_work",
                        args=(),
                        kwargs={},
                        options={},
                    )
                )
            broker.enqueue(
                Message(
                    queue_name=queue,
                    actor_name="do_work",
                    args=(99,),
                    kwargs={},
                    options={},
                ),
                delay=60_000,
            )
            inspect_conn = pika.BlockingConnection(pika.URLParameters(amqp_url))
            try:
                ch = inspect_conn.channel()
                main_count = ch.queue_declare(
                    queue=queue, passive=True
                ).method.message_count
                dq_count = ch.queue_declare(
                    queue=f"{queue}.DQ", passive=True
                ).method.message_count
            finally:
                if inspect_conn.is_open:
                    inspect_conn.close()
            assert main_count == 2
            assert dq_count == 1
            assert job_queue_size(queue, broker_url=amqp_url) == 2
            assert job_queue_size(queue, "missing_no_such_q", broker_url=amqp_url) == 2
        finally:
            broker.close()
    finally:
        _rmq_cleanup(queue)


def test_rabbitmq_job_queue_latency_head_age_and_requeue():
    queue = f"hf_dramatiq_lat_{uuid.uuid4().hex[:8]}"
    _rmq_cleanup(queue)
    try:
        from dramatiq.brokers.rabbitmq import RabbitmqBroker

        broker = RabbitmqBroker(url=amqp_url, confirm_delivery=True)
        try:
            broker.declare_queue(queue)
            now_ms = int(time.time() * 1000)
            broker.enqueue(
                Message(
                    queue_name=queue,
                    actor_name="do_work",
                    args=(),
                    kwargs={},
                    options={},
                    message_id=str(uuid.uuid4()),
                    message_timestamp=now_ms - 180_000,
                )
            )
            assert job_queue_latency(queue, broker_url=amqp_url) == pytest.approx(
                180, abs=10
            )
            assert job_queue_size(queue, broker_url=amqp_url) == 1
            assert job_queue_latency(queue, broker_url=amqp_url) == pytest.approx(
                180, abs=10
            )
        finally:
            broker.close()
    finally:
        _rmq_cleanup(queue)


def test_rabbitmq_missing_queue_size_and_latency_zero():
    assert job_queue_size("no_such_dramatiq_queue_xyz", broker_url=amqp_url) == 0
    assert job_queue_latency("no_such_dramatiq_queue_xyz", broker_url=amqp_url) == 0


def test_rabbitmq_missing_middle_queue_still_counts_siblings():
    """Channel recovery after 404 must not drop later queues in the same sample."""
    q_a = f"hf_dramatiq_mid_a_{uuid.uuid4().hex[:8]}"
    q_b = f"hf_dramatiq_mid_b_{uuid.uuid4().hex[:8]}"
    missing = f"hf_dramatiq_mid_missing_{uuid.uuid4().hex[:8]}"
    _rmq_cleanup(q_a)
    try:
        _rmq_cleanup(q_b)
        try:
            from dramatiq.brokers.rabbitmq import RabbitmqBroker

            broker = RabbitmqBroker(url=amqp_url, confirm_delivery=True)
            try:
                broker.declare_queue(q_a)
                broker.declare_queue(q_b)
                for _ in range(2):
                    broker.enqueue(
                        Message(
                            queue_name=q_a,
                            actor_name="do_work",
                            args=(),
                            kwargs={},
                            options={},
                        )
                    )
                broker.enqueue(
                    Message(
                        queue_name=q_b,
                        actor_name="do_work",
                        args=(),
                        kwargs={},
                        options={},
                    )
                )
                assert job_queue_size(q_a, missing, q_b, broker_url=amqp_url) == 3
                assert job_queue_size(missing, q_b, broker_url=amqp_url) == 1
            finally:
                broker.close()
        finally:
            _rmq_cleanup(q_b)
    finally:
        _rmq_cleanup(q_a)


def test_rabbitmq_latency_requeues_even_when_body_corrupt():
    queue = f"hf_dramatiq_corrupt_{uuid.uuid4().hex[:8]}"
    connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
    try:
        channel = connection.channel()
        channel.queue_declare(queue=queue, durable=False, auto_delete=True)
        channel.confirm_delivery()
        channel.basic_publish(
            exchange="",
            routing_key=queue,
            body=b"not-json-{{{",
            mandatory=True,
        )
        assert (
            channel.queue_declare(queue=queue, passive=True).method.message_count == 1
        )

        assert job_queue_latency(queue, broker_url=amqp_url) == 0
        assert job_queue_size(queue, broker_url=amqp_url) == 1
    finally:
        try:
            if connection.is_open:
                try:
                    channel = connection.channel()
                    channel.queue_delete(queue)
                except Exception:
                    pass
        finally:
            if connection.is_open:
                try:
                    connection.close()
                except Exception:
                    pass


def test_rabbitmq_broker_injection():
    queue = f"hf_dramatiq_broker_inj_{uuid.uuid4().hex[:8]}"
    _rmq_cleanup(queue)
    try:
        from dramatiq.brokers.rabbitmq import RabbitmqBroker

        broker = RabbitmqBroker(url=amqp_url, confirm_delivery=True)
        try:
            broker.declare_queue(queue)
            broker.enqueue(
                Message(
                    queue_name=queue,
                    actor_name="do_work",
                    args=(),
                    kwargs={},
                    options={},
                )
            )
            assert job_queue_size(queue, broker=broker) == 1
        finally:
            broker.close()
    finally:
        _rmq_cleanup(queue)


def test_rabbitmq_job_queue_size_multi_queue_and_empty():
    q_a = f"hf_dramatiq_mq_a_{uuid.uuid4().hex[:8]}"
    q_b = f"hf_dramatiq_mq_b_{uuid.uuid4().hex[:8]}"
    q_empty = f"hf_dramatiq_mq_empty_{uuid.uuid4().hex[:8]}"
    _rmq_cleanup(q_a)
    try:
        _rmq_cleanup(q_b)
        try:
            _rmq_cleanup(q_empty)
            try:
                from dramatiq.brokers.rabbitmq import RabbitmqBroker

                broker = RabbitmqBroker(url=amqp_url, confirm_delivery=True)
                try:
                    broker.declare_queue(q_a)
                    broker.declare_queue(q_b)
                    broker.declare_queue(q_empty)
                    for _ in range(2):
                        broker.enqueue(
                            Message(
                                queue_name=q_a,
                                actor_name="do_work",
                                args=(),
                                kwargs={},
                                options={},
                            )
                        )
                    broker.enqueue(
                        Message(
                            queue_name=q_b,
                            actor_name="do_work",
                            args=(),
                            kwargs={},
                            options={},
                        )
                    )
                    broker.enqueue(
                        Message(
                            queue_name=q_a,
                            actor_name="do_work",
                            args=(1,),
                            kwargs={},
                            options={},
                        ),
                        delay=120_000,
                    )
                    assert job_queue_size(q_empty, broker_url=amqp_url) == 0
                    assert job_queue_size(q_a, broker_url=amqp_url) == 2
                    assert job_queue_size(q_b, broker_url=amqp_url) == 1
                    assert job_queue_size(q_a, q_b, q_empty, broker_url=amqp_url) == 3
                finally:
                    broker.close()
            finally:
                _rmq_cleanup(q_empty)
        finally:
            _rmq_cleanup(q_b)
    finally:
        _rmq_cleanup(q_a)


def test_rabbitmq_xq_not_counted_as_waiting():
    queue = f"hf_dramatiq_xq_{uuid.uuid4().hex[:8]}"
    _rmq_cleanup(queue)
    try:
        connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        try:
            channel = connection.channel()
            channel.queue_declare(queue=queue, durable=False, auto_delete=True)
            channel.queue_declare(queue=f"{queue}.XQ", durable=False, auto_delete=True)
            channel.confirm_delivery()
            channel.basic_publish(
                exchange="",
                routing_key=f"{queue}.XQ",
                body=_encode(queue=queue),
                mandatory=True,
            )
            xq_count = channel.queue_declare(
                queue=f"{queue}.XQ", passive=True
            ).method.message_count
            main_count = channel.queue_declare(
                queue=queue, passive=True
            ).method.message_count
            assert xq_count == 1
            assert main_count == 0
            assert job_queue_size(queue, broker_url=amqp_url) == 0
            assert job_queue_latency(queue, broker_url=amqp_url) == 0
        finally:
            if connection.is_open:
                connection.close()
    finally:
        _rmq_cleanup(queue)


def test_rabbitmq_samples_only_main_queue_names_not_dq_or_xq(monkeypatch):
    """JQS/JQL must passive-declare / basic_get only the canonical queue name."""
    declared: list[tuple[str, bool]] = []
    got: list[tuple[str, bool]] = []

    class Method:
        message_count = 3
        delivery_tag = 1

    class Result:
        method = Method()

    class Channel:
        is_closed = False

        def queue_declare(self, queue, passive=False):
            declared.append((queue, passive))
            return Result()

        def basic_get(self, queue, auto_ack=False):
            got.append((queue, auto_ack))
            body = _encode(
                queue=queue,
                message_timestamp=int(time.time() * 1000) - 90_000,
            )
            return Method(), None, body

        def basic_reject(self, delivery_tag, requeue=True):
            assert requeue is True
            assert delivery_tag == 1

    class Connection:
        is_open = True

        def channel(self):
            return Channel()

        def close(self):
            self.is_open = False

    def fake_blocking(parameters=None, **kwargs):
        return Connection()

    monkeypatch.setattr(dramatiq_macro.pika, "BlockingConnection", fake_blocking)
    monkeypatch.setattr(dramatiq_macro.pika, "URLParameters", lambda url: {"url": url})

    assert job_queue_size("orders", "mail", broker_url=amqp_url) == 6
    assert job_queue_latency("orders", broker_url=amqp_url) == pytest.approx(90, abs=5)

    assert {(name, passive) for name, passive in declared} == {
        ("orders", True),
        ("mail", True),
    }
    assert len(declared) == 2
    assert all(passive is True for _, passive in declared)
    assert got == [("orders", False)]
    assert not any(name.endswith(".DQ") or name.endswith(".XQ") for name, _ in declared)
    assert not any(name.endswith(".DQ") or name.endswith(".XQ") for name, _ in got)


def test_rabbitmq_multi_queue_latency_max(monkeypatch):
    """Multi-queue RMQ JQL must take max head age across queues, not first only."""
    ages = {"slow": 200, "fast": 40}
    got: list[str] = []

    class Method:
        def __init__(self, tag):
            self.delivery_tag = tag

    class Channel:
        is_closed = False
        _tag = 0

        def basic_get(self, queue, auto_ack=False):
            assert auto_ack is False
            got.append(queue)
            self._tag += 1
            body = _encode(
                queue=queue,
                message_timestamp=int(time.time() * 1000) - int(ages[queue] * 1000),
            )
            return Method(self._tag), None, body

        def basic_reject(self, delivery_tag, requeue=True):
            assert requeue is True

    class Connection:
        is_open = True

        def channel(self):
            return Channel()

        def close(self):
            self.is_open = False

    monkeypatch.setattr(
        dramatiq_macro.pika, "BlockingConnection", lambda *a, **k: Connection()
    )
    monkeypatch.setattr(dramatiq_macro.pika, "URLParameters", lambda url: {"url": url})

    assert job_queue_latency("fast", "slow", broker_url=amqp_url) == pytest.approx(
        200, abs=5
    )
    assert set(got) == {"fast", "slow"}


def test_rabbitmq_url_owned_connection_closed(monkeypatch):
    closed = {"n": 0}

    class Method:
        message_count = 0

    class Result:
        method = Method()

    class Channel:
        is_closed = False

        def queue_declare(self, queue, passive=False):
            return Result()

        def basic_get(self, queue, auto_ack=False):
            return None, None, None

    class Connection:
        is_open = True

        def channel(self):
            return Channel()

        def close(self):
            closed["n"] += 1
            self.is_open = False

    monkeypatch.setattr(
        dramatiq_macro.pika, "BlockingConnection", lambda *a, **k: Connection()
    )
    monkeypatch.setattr(dramatiq_macro.pika, "URLParameters", lambda url: {"url": url})

    assert job_queue_size("default", broker_url=amqp_url) == 0
    assert closed["n"] == 1
    closed["n"] = 0
    assert job_queue_latency("default", broker_url=amqp_url) == 0
    assert closed["n"] == 1


def test_rabbitmq_connect_fail_returns_zero_and_does_not_raise(monkeypatch):
    """Production-relevant: pika connect failure must sample as 0, not raise."""

    def boom(*args, **kwargs):
        raise dramatiq_macro.AMQPConnectionError("refused")

    monkeypatch.setattr(dramatiq_macro.pika, "BlockingConnection", boom)
    monkeypatch.setattr(dramatiq_macro.pika, "URLParameters", lambda url: {"url": url})
    assert job_queue_size("default", broker_url=amqp_url) == 0
    assert job_queue_latency("default", broker_url=amqp_url) == 0.0


def test_unreachable_amqp_returns_zero():
    assert job_queue_size("default", broker_url="amqp://guest:guest@127.0.0.1:1/") == 0
    assert (
        job_queue_latency("default", broker_url="amqp://guest:guest@127.0.0.1:1/") == 0
    )


def test_rabbitmq_latency_always_rejects_even_when_reject_raises(monkeypatch):
    """Reject failures must not leave the sample path unhandled (best-effort requeue)."""
    rejected = {"n": 0}

    class Method:
        delivery_tag = 7

    class Channel:
        is_closed = False

        def basic_get(self, queue, auto_ack=False):
            body = _encode(
                queue=queue,
                message_timestamp=int(time.time() * 1000) - 30_000,
            )
            return Method(), None, body

        def basic_reject(self, delivery_tag, requeue=True):
            rejected["n"] += 1
            assert delivery_tag == 7
            assert requeue is True
            raise RuntimeError("channel closed mid-reject")

    class Connection:
        is_open = True

        def channel(self):
            return Channel()

        def close(self):
            self.is_open = False

    monkeypatch.setattr(
        dramatiq_macro.pika, "BlockingConnection", lambda *a, **k: Connection()
    )
    monkeypatch.setattr(dramatiq_macro.pika, "URLParameters", lambda url: {"url": url})

    assert job_queue_latency("default", broker_url=amqp_url) == pytest.approx(30, abs=5)
    assert rejected["n"] == 1


def test_rabbitmq_channel_404_recovers_for_next_queue(monkeypatch):
    """Passive 404 closes the channel; next queue must open a fresh one."""
    state = {"channels": 0, "declares": []}

    class Method:
        message_count = 4

    class Result:
        method = Method()

    class Channel:
        def __init__(self):
            self.is_closed = False

        def queue_declare(self, queue, passive=False):
            state["declares"].append(queue)
            if queue == "gone":
                self.is_closed = True
                raise dramatiq_macro.AMQPChannelError("404 NOT_FOUND")
            return Result()

    class Connection:
        is_open = True

        def channel(self):
            state["channels"] += 1
            return Channel()

        def close(self):
            self.is_open = False

    monkeypatch.setattr(
        dramatiq_macro.pika, "BlockingConnection", lambda *a, **k: Connection()
    )
    monkeypatch.setattr(dramatiq_macro.pika, "URLParameters", lambda url: {"url": url})

    assert job_queue_size("gone", "alive", broker_url=amqp_url) == 4
    assert "gone" in state["declares"]
    assert "alive" in state["declares"]
    assert state["channels"] >= 2
