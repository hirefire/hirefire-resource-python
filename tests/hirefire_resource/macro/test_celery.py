import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from celery import Celery, current_app
from kombu import Queue, pools

from hirefire_resource import HireFire, plan
from hirefire_resource.errors import (
    MissingQueueError,
    SampleIncompleteError,
    SampleNotReadyError,
)
from hirefire_resource.macro import celery as celery_macro
from hirefire_resource.macro.celery import (
    ChannelError,
    _job_queue_latency_rabbitmq,
    _job_queue_latency_redis,
    _resolve_broker_url,
    async_job_queue_latency,
    async_job_queue_size,
    async_job_queue_working,
    job_queue_latency,
    job_queue_size,
    job_queue_working,
)
from tests.helpers import missing_public_names, sample_plan

redis_url = f"redis://127.0.0.1:{os.environ.get('REDIS_PORT', '6379')}/0"
amqp_url = f"amqp://guest:guest@127.0.0.1:{os.environ.get('RABBITMQ_PORT', '5672')}"
broker_urls = [redis_url, amqp_url]

_SIZE_WAIT_S = 2.0
_SIZE_POLL_S = 0.02


def _celery(broker_url):
    app = Celery(broker=broker_url)
    app.conf.broker_pool_limit = None
    if str(broker_url).startswith("amqp"):
        app.conf.broker_transport_options = {"confirm_publish": True}
    return app


def _assert_int_count(value):
    assert isinstance(value, int) and not isinstance(value, bool), type(value)


def _assert_float_seconds(value):
    assert isinstance(value, float), type(value)


def _assert_latency(latency, age, since):
    waited = (datetime.now(timezone.utc) - since).total_seconds()
    _assert_float_seconds(latency)
    assert age - 1 <= latency <= age + waited + 1


def _assert_size(expected, *queues, **kwargs):
    deadline = time.monotonic() + _SIZE_WAIT_S
    seen = None
    while time.monotonic() < deadline:
        try:
            seen = job_queue_size(*queues, **kwargs)
        except SampleIncompleteError as error:
            seen = error
        else:
            _assert_int_count(seen)
            if seen == expected:
                return
        time.sleep(_SIZE_POLL_S)
    assert seen == expected


async def _assert_async_size(expected, *queues, **kwargs):
    deadline = time.monotonic() + _SIZE_WAIT_S
    seen = None
    while time.monotonic() < deadline:
        try:
            seen = await async_job_queue_size(*queues, **kwargs)
        except SampleIncompleteError as error:
            seen = error
        else:
            _assert_int_count(seen)
            if seen == expected:
                return
        await asyncio.sleep(_SIZE_POLL_S)
    assert seen == expected


def _held_threads():
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "hirefire-celery-held-tasks"
    ]


@pytest.fixture(scope="session", params=broker_urls)
def celery_app(request):
    return _celery(request.param)


@pytest.fixture(autouse=True)
def fast_held_tasks(monkeypatch):
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_INSPECT_TIMEOUT", 0.05)
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_REFRESH_INTERVAL", 0.05)
    yield
    celery_macro.reinit_after_fork()
    for thread in _held_threads():
        thread.join(10)
    assert _held_threads() == []


@pytest.fixture(autouse=True)
def clear_broker(celery_app):
    broker_url = celery_app.conf.broker_url

    if broker_url.startswith("redis://"):
        with celery_app.connection_or_acquire() as connection:
            connection.default_channel.client.flushdb()

    elif broker_url.startswith("amqp://"):
        with celery_app.connection_or_acquire() as connection:
            channel = connection.default_channel
            for queue in ["celery", "mailer"]:
                channel.queue_delete(queue=queue)
                channel.queue_declare(queue=queue, durable=True, auto_delete=False)


def test_library_loaded_is_true_when_celery_package_is_imported():
    assert plan.load_macro("celery") is not None
    assert plan.any_library_loaded()


def test_plan_does_not_import_celery_when_host_is_unloaded():
    import sys

    removed = {}
    keys = [
        k
        for k in list(sys.modules)
        if k == "celery"
        or k.startswith("celery.")
        or k == "hirefire_resource.macro.celery"
    ]
    for key in keys:
        removed[key] = sys.modules.pop(key)
    try:
        assert plan.load_macro("celery") is None
        with plan.around_job_queue_sample(HireFire.configuration.logger):
            pass
        plan.reinit_macros_after_fork(HireFire.configuration.logger)
        assert "celery" not in sys.modules
        assert "hirefire_resource.macro.celery" not in sys.modules
    finally:
        sys.modules.update(removed)


def test_job_queue_latency_missing_queue():
    with pytest.raises(MissingQueueError):
        job_queue_latency()


def test_job_queue_latency_without_jobs(celery_app):
    latency = job_queue_latency("celery", broker_url=celery_app.conf.broker_url)
    _assert_float_seconds(latency)
    assert latency == 0


def enqueue_for_job_queue_latency_with_job(celery_app):
    now = datetime.now(timezone.utc)

    for i in reversed(range(5)):
        celery_app.send_task(
            "test_task", queue="celery", eta=(now - timedelta(seconds=i))
        )
        celery_app.send_task(
            "test_task", queue="mailer", eta=(now - timedelta(seconds=i * 2))
        )
    return now


def test_job_queue_latency_with_jobs(celery_app):
    enqueued_at = enqueue_for_job_queue_latency_with_job(celery_app)

    _assert_latency(
        job_queue_latency("celery", broker_url=celery_app.conf.broker_url),
        4,
        enqueued_at,
    )
    _assert_latency(
        job_queue_latency("mailer", broker_url=celery_app.conf.broker_url),
        8,
        enqueued_at,
    )
    _assert_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url, skip_working=True
    )


BOOT_THEN_PUBLISH = """
import sys
from datetime import datetime, timedelta, timezone
from celery import Celery
from hirefire_resource import HireFire

HireFire.boot()
app = Celery(broker=sys.argv[1])
eta = datetime.now(timezone.utc) - timedelta(seconds=30)
app.send_task("test_task", queue="celery", eta=eta)
HireFire.configuration.stop_dispatcher(flush=False)
"""

BOOT_BEFORE_CELERY_THEN_PUBLISH = """
import sys
import time
from datetime import datetime, timedelta, timezone
from hirefire_resource import HireFire

HireFire.boot()
from celery import Celery

app = Celery(broker=sys.argv[1])
for _ in range(200):
    macro = sys.modules.get("hirefire_resource.macro.celery")
    if getattr(macro, "plan_connection_options", None) is not None:
        break
    time.sleep(0.05)
eta = datetime.now(timezone.utc) - timedelta(seconds=30)
app.send_task("test_task", queue="celery", eta=eta)
HireFire.configuration.stop_dispatcher(flush=False)
"""


def _publish_from_another_process(script, broker_url, dyno):
    published_at = datetime.now(timezone.utc)
    subprocess.run(
        [sys.executable, "-c", script, broker_url],
        env={
            **os.environ,
            "HIREFIRE_TOKEN": "test-token",
            "HIREFIRE_DATA_URL": "http://127.0.0.1:9",
            "DYNO": dyno,
        },
        check=True,
        capture_output=True,
        timeout=60,
    )
    return published_at


def test_a_process_that_boots_with_celery_loaded_writes_the_time_a_task_is_due(
    celery_app,
):
    broker_url = celery_app.conf.broker_url

    published_at = _publish_from_another_process(BOOT_THEN_PUBLISH, broker_url, "web.1")

    _assert_latency(
        job_queue_latency("celery", broker_url=broker_url), 30, published_at
    )


def test_a_process_that_loads_celery_after_its_boot_writes_the_time_a_task_is_due(
    celery_app,
):
    broker_url = celery_app.conf.broker_url

    published_at = _publish_from_another_process(
        BOOT_BEFORE_CELERY_THEN_PUBLISH, broker_url, "worker.1"
    )

    _assert_latency(
        job_queue_latency("celery", broker_url=broker_url), 30, published_at
    )


def test_a_task_published_with_protocol_one_carries_the_time_it_is_due(celery_app):
    broker_url = celery_app.conf.broker_url
    app = _celery(broker_url)
    app.conf.task_protocol = 1
    published_at = datetime.now(timezone.utc)

    app.send_task("test_task", queue="celery", eta=published_at - timedelta(seconds=30))

    _assert_latency(
        job_queue_latency("celery", broker_url=broker_url), 30, published_at
    )


def test_the_publish_handler_writes_into_an_empty_header_mapping():
    headers = {}

    celery_macro.run_at_header_signal(headers=headers, body={"eta": None})

    assert datetime.fromisoformat(headers["run_at"]).tzinfo is not None


def test_the_publish_handler_does_nothing_for_a_publish_without_headers():
    assert celery_macro.run_at_header_signal(headers=None, body={"eta": None}) is None


def test_the_publish_handler_takes_the_time_from_the_body_of_a_protocol_one_task():
    headers = {}

    celery_macro.run_at_header_signal(
        headers=headers, body={"eta": "2026-01-01T00:00:00+00:00"}
    )

    assert headers == {"run_at": "2026-01-01T00:00:00+00:00"}


def test_job_queue_latency_with_jobs_multi(celery_app):
    enqueued_at = enqueue_for_job_queue_latency_with_job(celery_app)

    _assert_latency(
        job_queue_latency("celery", "mailer", broker_url=celery_app.conf.broker_url),
        8,
        enqueued_at,
    )
    _assert_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url, skip_working=True
    )


@pytest.mark.asyncio
async def test_async_job_queue_latency_missing_queue():
    with pytest.raises(MissingQueueError):
        await async_job_queue_latency()


@pytest.mark.asyncio
async def test_job_queue_latency_without_jobs_async(celery_app):
    latency = await async_job_queue_latency(
        "celery", broker_url=celery_app.conf.broker_url
    )
    _assert_float_seconds(latency)
    assert latency == 0


@pytest.mark.asyncio
async def test_job_queue_latency_with_jobs_async(celery_app):
    enqueued_at = enqueue_for_job_queue_latency_with_job(celery_app)

    _assert_latency(
        await async_job_queue_latency("celery", broker_url=celery_app.conf.broker_url),
        4,
        enqueued_at,
    )
    _assert_latency(
        await async_job_queue_latency("mailer", broker_url=celery_app.conf.broker_url),
        8,
        enqueued_at,
    )
    await _assert_async_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url, skip_working=True
    )


@pytest.mark.asyncio
async def test_job_queue_latency_with_jobs_multi_async(celery_app):
    enqueued_at = enqueue_for_job_queue_latency_with_job(celery_app)

    _assert_latency(
        await async_job_queue_latency(
            "celery", "mailer", broker_url=celery_app.conf.broker_url
        ),
        8,
        enqueued_at,
    )
    await _assert_async_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url, skip_working=True
    )


def test_job_queue_size_missing_queue():
    with pytest.raises(MissingQueueError):
        job_queue_size()


def test_job_queue_size_without_jobs(celery_app):
    size = job_queue_size(
        "celery", broker_url=celery_app.conf.broker_url, skip_working=True
    )
    _assert_int_count(size)
    assert size == 0


def test_job_queue_size_with_jobs(celery_app):
    for _ in range(5):
        celery_app.send_task("test_task", queue="celery")
        celery_app.send_task("test_task", queue="mailer")

    _assert_size(5, "celery", broker_url=celery_app.conf.broker_url, skip_working=True)
    _assert_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url, skip_working=True
    )


def test_job_queue_size_dedupes_and_trims_queue_names(celery_app):
    for _ in range(3):
        celery_app.send_task("test_task", queue="celery")

    _assert_size(
        3,
        "celery",
        " celery ",
        "celery",
        broker_url=celery_app.conf.broker_url,
        skip_working=True,
    )


def test_job_queue_size_blank_queue_names_raise():
    with pytest.raises(MissingQueueError):
        job_queue_size("  ", "")


def test_mitigate_connection_reset_error_decorator():
    from hirefire_resource.macro.celery import mitigate_connection_reset_error

    call_count = [0]

    @mitigate_connection_reset_error(retries=2, delay=0)
    def flaky_function():
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionResetError("Simulated failure")
        return 42

    result = flaky_function()
    assert result == 42
    assert call_count[0] == 2


def test_mitigate_connection_reset_error_retries_zero_still_calls_once():
    from hirefire_resource.macro.celery import mitigate_connection_reset_error

    call_count = [0]

    @mitigate_connection_reset_error(retries=0, delay=0)
    def once():
        call_count[0] += 1
        return 1

    assert once() == 1
    assert call_count[0] == 1


def test_a_connection_reset_on_every_attempt_is_raised_after_the_last_one():
    calls = []

    @celery_macro.mitigate_connection_reset_error(retries=3)
    def always_reset():
        calls.append(True)
        raise ConnectionResetError("reset")

    with pytest.raises(ConnectionResetError):
        always_reset()

    assert len(calls) == 3


def test_a_delay_between_attempts_is_slept_when_one_is_given(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    calls = []

    @celery_macro.mitigate_connection_reset_error(retries=3, delay=0.5)
    def resets_twice():
        calls.append(True)
        if len(calls) < 3:
            raise ConnectionResetError("reset")
        return "done"

    assert resets_twice() == "done"
    assert slept == [0.5, 0.5]


def test_an_error_other_than_a_connection_reset_is_not_retried():
    calls = []

    @celery_macro.mitigate_connection_reset_error(retries=3)
    def broken():
        calls.append(True)
        raise BrokenPipeError("pipe")

    with pytest.raises(BrokenPipeError):
        broken()

    assert len(calls) == 1


def test_plan_drops_sample_when_celery_raises_operational_error(monkeypatch, caplog):
    import logging
    from contextlib import contextmanager

    from kombu.exceptions import OperationalError

    from hirefire_resource.macro import celery as celery_macro

    caplog.set_level(logging.ERROR)

    @contextmanager
    def boom(*_args, **_kwargs):
        raise OperationalError("broker down")
        yield None

    monkeypatch.setattr(celery_macro, "_sample_connection", boom)
    with patch.object(plan, "load_macro", return_value=celery_macro):
        sample_plan(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jqs",
                "queues": ["celery"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert data.get("worker", {}).get("jqs") is None
    assert "OperationalError" in caplog.text
    assert "broker down" in caplog.text


def test_job_queue_size_reraises_operational_error(monkeypatch):
    from contextlib import contextmanager

    from kombu.exceptions import OperationalError

    @contextmanager
    def boom():
        raise OperationalError("broker down")
        yield None

    monkeypatch.setattr(
        "hirefire_resource.macro.celery._sample_connection",
        lambda _app, _url: boom(),
    )
    with pytest.raises(OperationalError, match="broker down"):
        job_queue_size("celery", broker_url="redis://localhost:6379/0")
    assert celery_macro._held_tasks == {}


def test_job_queue_latency_reraises_operational_error(monkeypatch):
    from contextlib import contextmanager

    from kombu.exceptions import OperationalError

    @contextmanager
    def boom():
        raise OperationalError("broker down")
        yield None

    monkeypatch.setattr(
        "hirefire_resource.macro.celery._sample_connection",
        lambda _app, _url: boom(),
    )
    with pytest.raises(OperationalError, match="broker down"):
        job_queue_latency("celery", broker_url="redis://localhost:6379/0")


def test_mitigate_connection_reset_error_preserves_name_and_retries_once(
    monkeypatch,
):
    from hirefire_resource.macro import celery as celery_macro

    assert celery_macro.job_queue_size.__name__ == "job_queue_size"
    assert celery_macro.job_queue_latency.__name__ == "job_queue_latency"

    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    calls = [0]

    @celery_macro.mitigate_connection_reset_error()
    def flaky_function():
        calls[0] += 1
        if calls[0] == 1:
            raise ConnectionResetError("reset")
        return 7

    assert flaky_function() == 7
    assert calls[0] == 2
    assert slept == []


@pytest.mark.asyncio
async def test_async_job_queue_size_missing_queue():
    with pytest.raises(MissingQueueError):
        await async_job_queue_size()


@pytest.mark.asyncio
async def test_job_queue_size_without_jobs_async(celery_app):
    size = await async_job_queue_size(
        "celery", broker_url=celery_app.conf.broker_url, skip_working=True
    )
    _assert_int_count(size)
    assert size == 0


@pytest.mark.asyncio
async def test_job_queue_size_with_jobs_async(celery_app):
    for _ in range(5):
        celery_app.send_task("test_task", queue="celery")
        celery_app.send_task("test_task", queue="mailer")

    await _assert_async_size(
        5, "celery", broker_url=celery_app.conf.broker_url, skip_working=True
    )
    await _assert_async_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url, skip_working=True
    )


@pytest.fixture
def quorum_queue():
    name = f"hf_celery_quorum_{uuid.uuid4().hex[:8]}"
    arguments = {"x-queue-type": "quorum"}
    app = _celery(amqp_url)
    app.conf.task_queues = [Queue(name, queue_arguments=arguments)]
    with app.connection_or_acquire() as connection:
        connection.default_channel.queue_declare(
            queue=name, durable=True, auto_delete=False, arguments=arguments
        )

    yield app, name

    with app.connection_or_acquire() as connection:
        connection.default_channel.queue_delete(queue=name)


def test_latency_samples_leave_the_head_message_of_a_quorum_queue_in_the_queue(
    quorum_queue,
):
    app, name = quorum_queue
    app.send_task("test_task", queue=name)
    _assert_size(1, name, celery_app=app, skip_working=True)

    for _ in range(25):
        assert job_queue_latency(name, broker_url=amqp_url) >= 0

    assert job_queue_size(name, celery_app=app, skip_working=True) == 1


@pytest.fixture
def priority_celery_app():
    app = _celery(amqp_url)

    queue_arguments = {"x-max-priority": 10}
    app.conf.task_queues = [
        Queue("priority_queue", queue_arguments=queue_arguments),
    ]
    app.conf.task_queue_max_priority = 10
    app.conf.task_default_priority = 5

    return app


@pytest.fixture
def setup_priority_queue(priority_celery_app):
    with priority_celery_app.connection_or_acquire() as connection:
        channel = connection.default_channel
        channel.queue_delete(queue="priority_queue")
        channel.queue_declare(
            queue="priority_queue",
            durable=True,
            auto_delete=False,
            arguments={"x-max-priority": 10},
        )

    yield priority_celery_app

    with priority_celery_app.connection_or_acquire() as connection:
        channel = connection.default_channel
        channel.queue_delete(queue="priority_queue")


def test_job_queue_size_reconnects_a_pooled_connection_that_kombu_closed(celery_app):
    app = _celery(celery_app.conf.broker_url)
    app.conf.broker_pool_limit = 2
    celery_app.send_task("test_task", queue="celery")
    previous = pools.get_limit()

    try:
        with pytest.raises(RuntimeError):
            with app.connection_or_acquire() as connection:
                connection.default_channel
                raise RuntimeError("raised inside the block")

        _assert_size(1, "celery", celery_app=app, skip_working=True)
    finally:
        pools.set_limit(previous)


def test_job_queue_size_priority_queue_with_broker_url(setup_priority_queue):
    priority_celery_app = setup_priority_queue
    broker_url = priority_celery_app.conf.broker_url

    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")

    _assert_size(2, "priority_queue", broker_url=broker_url, skip_working=True)


def test_job_queue_size_priority_queue_with_celery_app_returns_correct_count(
    setup_priority_queue,
):
    priority_celery_app = setup_priority_queue

    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")

    _assert_size(3, "priority_queue", celery_app=priority_celery_app, skip_working=True)


def test_job_queue_size_raises_error_when_both_broker_url_and_celery_app_provided(
    setup_priority_queue,
):
    priority_celery_app = setup_priority_queue

    with pytest.raises(ValueError) as exc_info:
        job_queue_size(
            "priority_queue",
            broker_url=amqp_url,
            celery_app=priority_celery_app,
        )

    assert "Cannot specify both" in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_job_queue_size_raises_error_when_both_broker_url_and_celery_app_provided(
    setup_priority_queue,
):
    priority_celery_app = setup_priority_queue

    with pytest.raises(ValueError) as exc_info:
        await async_job_queue_size(
            "priority_queue",
            broker_url=amqp_url,
            celery_app=priority_celery_app,
        )

    assert "Cannot specify both" in str(exc_info.value)


def test_job_queue_size_with_mismatched_priority_arguments(celery_app):
    broker_url = celery_app.conf.broker_url

    if not broker_url.startswith("amqp"):
        pytest.skip("Priority queues only work with RabbitMQ")

    queue_name = f"test_mismatch_{uuid.uuid4().hex[:8]}"

    temp_app = _celery(broker_url)
    temp_app.conf.task_queues = [
        Queue(queue_name, queue_arguments={"x-max-priority": 20}),
    ]

    try:
        with temp_app.connection_or_acquire() as connection:
            channel = connection.default_channel
            channel.queue_declare(
                queue=queue_name,
                durable=True,
                auto_delete=False,
                arguments={"x-max-priority": 20},
            )

        temp_app.send_task("test_task", queue=queue_name)
        temp_app.send_task("test_task", queue=queue_name)

        wrong_app = Celery(broker=broker_url)
        wrong_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 10}),
        ]

        result_wrong = job_queue_size(
            queue_name, celery_app=wrong_app, skip_working=True
        )
        assert result_wrong in [0, 2]

        correct_app = Celery(broker=broker_url)
        correct_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 20}),
        ]

        _assert_size(2, queue_name, celery_app=correct_app, skip_working=True)

    finally:
        try:
            with temp_app.connection_or_acquire() as connection:
                channel = connection.default_channel
                channel.queue_delete(queue=queue_name)
        except Exception:
            pass


def test_job_queue_latency_redis_accepts_str_payload():
    now = datetime.now(timezone.utc)
    run_at = (now - timedelta(seconds=5)).isoformat()
    payload = (
        '{"headers": {"run_at": "'
        + run_at
        + '"}, "body": "", "content-type": "application/json"}'
    )

    class FakeClient:
        def lindex(self, queue, index):
            return payload

    class FakeChannel:
        client = FakeClient()

    _assert_latency(_job_queue_latency_redis(FakeChannel(), "celery"), 5, now)


def test_job_queue_latency_redis_skips_corrupt_payload():
    class FakeClient:
        def lindex(self, queue, index):
            return "not-json"

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_queue_arguments_are_read_for_the_sampled_queues_only():
    app = Celery(broker=redis_url)
    app.conf.task_queues = [
        Queue("sampled", queue_arguments={"x-max-priority": 5}),
        Queue("other", queue_arguments={"x-max-priority": 9}),
    ]

    arguments = celery_macro._get_queue_arguments_from_app(app, {"sampled"})

    assert arguments == {"sampled": {"x-max-priority": 5}}


def test_a_redis_task_that_no_client_stamped_reads_as_no_latency():
    class FakeClient:
        def lindex(self, queue, index):
            return '{"headers": {"task": "app.work"}, "body": ""}'

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")

    _assert_float_seconds(latency)
    assert latency == 0


def test_a_rabbitmq_task_that_no_client_stamped_reads_as_no_latency_and_goes_back():
    class Message:
        headers = {"task": "app.work"}
        delivery_tag = 7

    class FakeChannel:
        def __init__(self):
            self.sent = []

        def basic_get(self, queue):
            return Message()

        def send_method(self, method, format, arguments):
            self.sent.append((method, format, arguments))

    channel = FakeChannel()
    latency = _job_queue_latency_rabbitmq(channel, "celery")

    _assert_float_seconds(latency)
    assert latency == 0
    assert channel.sent == [((60, 120), "Lbb", (7, False, True))]


def test_job_queue_latency_redis_corrupt_queue_does_not_hide_sibling():
    now = datetime.now(timezone.utc)
    run_at = (now - timedelta(seconds=8)).isoformat()
    payload = '{"headers": {"run_at": "' + run_at + '"}}'

    class FakeClient:
        def lindex(self, queue, index):
            return "not-json" if queue == "bad" else payload

    class FakeChannel:
        client = FakeClient()

    channel = FakeChannel()
    bad = _job_queue_latency_redis(channel, "bad")
    good = _job_queue_latency_redis(channel, "good")
    _assert_float_seconds(bad)
    assert bad == 0
    _assert_latency(good, 8, now)


def test_a_sample_leaves_the_current_app_of_its_thread_in_place():
    previous = current_app._get_current_object()
    host = Celery("host", broker=redis_url)
    try:
        assert current_app.main == "host"

        job_queue_size("celery", broker_url=redis_url, skip_working=True)
        job_queue_latency("celery", broker_url=redis_url)

        assert current_app.main == "host"
    finally:
        host.close()
        previous.set_current()


def test_job_queue_latency_rabbitmq_requeues_after_parse_error():
    class BrokenMessage:
        headers = object()
        delivery_tag = 7

    class FakeChannel:
        def __init__(self):
            self.sent = []

        def basic_get(self, queue):
            return BrokenMessage()

        def send_method(self, method, format, arguments):
            self.sent.append((method, format, arguments))

    channel = FakeChannel()
    latency = _job_queue_latency_rabbitmq(channel, "celery")
    _assert_float_seconds(latency)
    assert latency == 0

    assert channel.sent == [((60, 120), "Lbb", (7, False, True))]


def test_job_queue_latency_redis_empty_queue_is_float_zero():
    class FakeClient:
        def lindex(self, queue, index):
            return None

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_job_queue_latency_redis_future_run_at_is_float_zero():
    run_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    payload = '{"headers": {"run_at": "' + run_at + '"}}'

    class FakeClient:
        def lindex(self, queue, index):
            return payload

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_job_queue_latency_rabbitmq_empty_queue_is_float_zero():
    class FakeChannel:
        def basic_get(self, queue):
            return None

    latency = _job_queue_latency_rabbitmq(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_job_queue_latency_rabbitmq_channel_error_is_float_zero():
    try:
        error = ChannelError("NOT_FOUND")
    except TypeError:
        error = ChannelError(404, "NOT_FOUND", (60, 20))

    class FakeChannel:
        def basic_get(self, queue):
            raise error

    latency = _job_queue_latency_rabbitmq(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def _held_tasks_control():
    active_task = {
        "delivery_info": {"routing_key": "celery"},
        "id": "active-1",
    }
    reserved_task = {
        "delivery_info": {"routing_key": "celery"},
        "id": "reserved-1",
    }
    mailer_task = {
        "delivery_info": {"routing_key": "mailer"},
        "id": "reserved-2",
    }

    class HeldTasksInspect:
        def active(self):
            return {"worker@host": [active_task, dict(active_task, id="active-2")]}

        def reserved(self):
            return {"worker@host": [reserved_task, mailer_task]}

    class TrackingControl:
        def __init__(self):
            self.inspect_calls = 0
            self.error = None

        def inspect(self, *args, **kwargs):
            self.inspect_calls += 1
            if self.error is not None:
                raise self.error
            return HeldTasksInspect()

    return TrackingControl()


def test_job_queue_size_counts_the_tasks_workers_hold(celery_app, monkeypatch):
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="mailer")
    control = _held_tasks_control()
    monkeypatch.setattr(celery_app, "control", control)

    _assert_size(5, "celery", celery_app=celery_app)
    _assert_size(2, "mailer", celery_app=celery_app)
    _assert_size(7, "celery", "mailer", celery_app=celery_app)
    assert control.inspect_calls > 0
    assert len(_held_threads()) == 1


def test_job_queue_size_without_broker_jobs_counts_the_tasks_workers_hold(
    celery_app, monkeypatch
):
    monkeypatch.setattr(celery_app, "control", _held_tasks_control())

    _assert_size(3, "celery", celery_app=celery_app)
    _assert_size(0, "other", celery_app=celery_app)


def test_job_queue_size_drops_the_first_sample_after_a_start(celery_app, monkeypatch):
    celery_app.send_task("test_task", queue="celery")
    monkeypatch.setattr(celery_app, "control", _held_tasks_control())

    with pytest.raises(SampleNotReadyError):
        job_queue_size("celery", celery_app=celery_app)

    _assert_size(4, "celery", celery_app=celery_app)


def test_job_queue_working_is_what_job_queue_size_adds(celery_app, monkeypatch):
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="mailer")
    monkeypatch.setattr(celery_app, "control", _held_tasks_control())
    _assert_size(5, "celery", celery_app=celery_app)
    _assert_size(7, "celery", "mailer", celery_app=celery_app)

    for queues in (("celery",), ("mailer",), ("celery", "mailer")):
        working = job_queue_working(*queues, celery_app=celery_app)
        _assert_int_count(working)
        size = job_queue_size(*queues, celery_app=celery_app)
        waiting = job_queue_size(*queues, celery_app=celery_app, skip_working=True)
        assert working == size - waiting

    assert job_queue_working("celery", celery_app=celery_app) == 3
    assert job_queue_working("mailer", celery_app=celery_app) == 1
    assert len(_held_threads()) == 1


def test_job_queue_working_starts_the_thread_and_drops_the_first_sample(
    celery_app, monkeypatch
):
    monkeypatch.setattr(celery_app, "control", _held_tasks_control())

    with pytest.raises(SampleNotReadyError):
        job_queue_working("celery", celery_app=celery_app)
    assert len(_held_threads()) == 1

    _assert_size(3, "celery", celery_app=celery_app)
    assert job_queue_working("celery", celery_app=celery_app) == 3
    assert len(_held_threads()) == 1


def test_job_queue_working_by_broker_url_counts_nothing_when_no_worker_answers(
    celery_app,
):
    broker_url = celery_app.conf.broker_url

    with pytest.raises(SampleIncompleteError):
        job_queue_working("celery", broker_url=broker_url)

    _assert_size(0, "celery", broker_url=broker_url)
    assert job_queue_working("celery", broker_url=broker_url) == 0
    assert list(celery_macro._held_tasks) == [broker_url]


def test_job_queue_working_missing_queue():
    with pytest.raises(MissingQueueError):
        job_queue_working()


@pytest.mark.asyncio
async def test_async_job_queue_working_counts_the_tasks_workers_hold(
    celery_app, monkeypatch
):
    monkeypatch.setattr(celery_app, "control", _held_tasks_control())
    await _assert_async_size(3, "celery", celery_app=celery_app)

    assert await async_job_queue_working("celery", celery_app=celery_app) == 3
    assert await async_job_queue_working("celery", "mailer", celery_app=celery_app) == 4


def test_job_queue_size_skip_working_never_asks_the_workers(celery_app, monkeypatch):
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="mailer")
    control = _held_tasks_control()
    monkeypatch.setattr(celery_app, "control", control)

    _assert_size(2, "celery", celery_app=celery_app, skip_working=True)
    _assert_size(3, "celery", "mailer", celery_app=celery_app, skip_working=True)
    _assert_size(2, "celery", broker_url=celery_app.conf.broker_url, skip_working=True)

    assert control.inspect_calls == 0
    assert celery_macro._held_tasks == {}
    assert _held_threads() == []


def test_job_queue_size_skip_working_false_or_none_counts_held_tasks(
    celery_app, monkeypatch
):
    monkeypatch.setattr(celery_app, "control", _held_tasks_control())

    _assert_size(3, "celery", celery_app=celery_app, skip_working=False)
    _assert_size(3, "celery", celery_app=celery_app, skip_working=None)


@pytest.mark.asyncio
async def test_async_job_queue_size_counts_held_tasks_unless_skipped(
    celery_app, monkeypatch
):
    celery_app.send_task("test_task", queue="celery")
    monkeypatch.setattr(celery_app, "control", _held_tasks_control())

    await _assert_async_size(1, "celery", celery_app=celery_app, skip_working=True)
    assert celery_macro._held_tasks == {}

    await _assert_async_size(4, "celery", celery_app=celery_app)


def test_job_queue_size_by_broker_url_counts_no_held_tasks_when_no_worker_answers(
    celery_app,
):
    broker_url = celery_app.conf.broker_url
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="celery")
    previous = pools.get_limit()
    pools.set_limit(7)

    try:
        with pytest.raises(SampleIncompleteError):
            job_queue_size("celery", broker_url=broker_url)

        _assert_size(2, "celery", broker_url=broker_url)
        assert list(celery_macro._held_tasks) == [broker_url]
        assert pools.get_limit() == 7
    finally:
        pools.set_limit(previous)


def test_job_queue_size_reports_a_failing_worker_count(celery_app, monkeypatch):
    from kombu.exceptions import OperationalError

    control = _held_tasks_control()
    control.error = OperationalError("broker down")
    monkeypatch.setattr(celery_app, "control", control)

    with pytest.raises(SampleIncompleteError):
        job_queue_size("celery", celery_app=celery_app)

    deadline = time.monotonic() + _SIZE_WAIT_S
    message = ""
    while "broker down" not in message and time.monotonic() < deadline:
        with pytest.raises(SampleIncompleteError) as raised:
            job_queue_size("celery", celery_app=celery_app)
        message = str(raised.value)
        time.sleep(_SIZE_POLL_S)

    assert message == (
        "Celery has no recent count of the tasks its workers hold. "
        "The last refresh raised OperationalError: broker down."
    )
    assert job_queue_size("celery", celery_app=celery_app, skip_working=True) == 0


def test_job_queue_size_drops_the_sample_when_the_counts_are_stale(
    celery_app, monkeypatch
):
    control = _held_tasks_control()
    monkeypatch.setattr(celery_app, "control", control)
    _assert_size(3, "celery", celery_app=celery_app)

    control.error = RuntimeError("workers unreachable")
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_MAX_AGE", 0.1)

    deadline = time.monotonic() + _SIZE_WAIT_S
    seen = None
    while "RuntimeError: workers unreachable" not in str(seen):
        assert time.monotonic() < deadline, seen
        try:
            seen = job_queue_size("celery", celery_app=celery_app)
        except SampleIncompleteError as error:
            seen = error
        time.sleep(_SIZE_POLL_S)

    assert isinstance(seen, SampleIncompleteError)


def _execute_until_recorded(entry, series):
    deadline = time.monotonic() + _SIZE_WAIT_S
    flushed = {}
    while series not in flushed.get("worker", {}) and time.monotonic() < deadline:
        time.sleep(_SIZE_POLL_S)
        sample_plan(entry)
        flushed = HireFire.configuration.buffer.flush()
    return flushed


def test_plan_skip_working_samples_the_broker_and_records_wrk_from_the_workers(
    celery_app, monkeypatch, caplog
):
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", celery_app.conf.broker_url)
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="celery")
    _assert_size(2, "celery", broker_url=celery_app.conf.broker_url, skip_working=True)
    entry = {
        "name": "worker",
        "adapter": "celery",
        "strategy": "jqs",
        "queues": ["celery"],
        "options": {"skip_working": True},
    }

    HireFire.configuration.buffer.flush()
    sample_plan(entry)

    flushed = HireFire.configuration.buffer.flush()
    assert list(flushed["worker"]["jqs"].values())[-1] == 2
    assert "wrk" not in flushed["worker"]
    assert len(_held_threads()) == 1

    flushed = _execute_until_recorded(entry, "wrk")
    assert list(flushed["worker"]["jqs"].values())[-1] == 2
    assert list(flushed["worker"]["wrk"].values())[-1] == 0
    assert len(_held_threads()) == 1
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "hirefire_resource"
        and "Plan samples for" not in record.getMessage()
    ] == [
        "[HireFire] Counting the tasks Celery workers hold. "
        "Samples that need the count start when the first count arrives."
    ]


def test_plan_without_skip_working_drops_samples_until_the_workers_are_counted(
    celery_app, monkeypatch, caplog
):
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", celery_app.conf.broker_url)
    celery_app.send_task("test_task", queue="celery")
    _assert_size(1, "celery", broker_url=celery_app.conf.broker_url, skip_working=True)
    entry = {
        "name": "worker",
        "adapter": "celery",
        "strategy": "jqs",
        "queues": ["celery"],
    }

    HireFire.configuration.buffer.flush()
    sample_plan(entry)

    def logged():
        return [
            (record.levelno, record.getMessage())
            for record in caplog.records
            if record.name == "hirefire_resource"
            and "Plan samples for" not in record.getMessage()
        ]

    assert HireFire.configuration.buffer.flush() == {}
    assert logged() == [
        (
            logging.INFO,
            "[HireFire] Counting the tasks Celery workers hold. "
            "Samples that need the count start when the first count arrives.",
        )
    ]

    deadline = time.monotonic() + _SIZE_WAIT_S
    flushed = {}
    while "jqs" not in flushed.get("worker", {}) and time.monotonic() < deadline:
        time.sleep(_SIZE_POLL_S)
        sample_plan(entry)
        flushed = HireFire.configuration.buffer.flush()

    assert list(flushed["worker"]["jqs"].values())[-1] == 1
    assert "wrk" not in flushed["worker"]
    assert len(logged()) == 1


def test_plan_jql_records_wrk_once_the_workers_are_counted(celery_app, monkeypatch):
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", celery_app.conf.broker_url)
    entry = {
        "name": "worker",
        "adapter": "celery",
        "strategy": "jql",
        "queues": ["celery"],
        "options": {"skip_working": True},
    }

    HireFire.configuration.buffer.flush()
    sample_plan(entry)

    flushed = HireFire.configuration.buffer.flush()
    assert list(flushed["worker"]["jql"].values())[-1] == 0
    assert "wrk" not in flushed["worker"]

    flushed = _execute_until_recorded(entry, "wrk")
    assert list(flushed["worker"]["jql"].values())[-1] == 0
    assert list(flushed["worker"]["wrk"].values())[-1] == 0
    assert len(_held_threads()) == 1


@contextmanager
def _worker(broker_url):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "--app",
            "celery_worker_app",
            "worker",
            "--pool",
            "threads",
            "--concurrency",
            "1",
            "--queues",
            "celery",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            "--loglevel",
            "ERROR",
        ],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env={**os.environ, "HIREFIRE_TEST_BROKER_URL": broker_url},
    )
    try:
        yield
    finally:
        process.kill()
        process.wait()


def test_job_queue_size_counts_the_tasks_a_real_worker_holds(celery_app, monkeypatch):
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_INSPECT_TIMEOUT", 1.0)
    monkeypatch.setattr(sys.modules[__name__], "_SIZE_WAIT_S", 30.0)
    broker_url = celery_app.conf.broker_url

    for _ in range(3):
        celery_app.send_task("hirefire.test.hold", queue="celery")
    celery_app.send_task(
        "hirefire.test.hold",
        queue="celery",
        eta=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    for _ in range(2):
        celery_app.send_task("hirefire.test.hold", queue="mailer")
    _assert_size(4, "celery", broker_url=broker_url, skip_working=True)

    with _worker(broker_url):
        _assert_size(0, "celery", broker_url=broker_url, skip_working=True)
        _assert_size(2, "celery", "mailer", celery_app=celery_app, skip_working=True)
        assert celery_macro._held_tasks == {}

        with pytest.raises(SampleIncompleteError):
            job_queue_size("celery", celery_app=celery_app)
        with pytest.raises(SampleIncompleteError):
            job_queue_size("celery", broker_url=broker_url)
        assert len(_held_threads()) == 2

        _assert_size(3, "celery", celery_app=celery_app)
        _assert_size(5, "celery", "mailer", celery_app=celery_app)
        _assert_size(3, "celery", broker_url=broker_url)
        _assert_size(5, "celery", "mailer", broker_url=broker_url)

        assert job_queue_working("celery", celery_app=celery_app) == 3
        assert job_queue_working("celery", "mailer", celery_app=celery_app) == 3
        assert job_queue_working("celery", broker_url=broker_url) == 3
        assert job_queue_working("mailer", broker_url=broker_url) == 0
        assert len(_held_threads()) == 2

        celery_app.send_task(
            "hirefire.test.hold",
            queue="celery",
            eta=datetime.now(timezone.utc) + timedelta(seconds=2),
        )
        _assert_size(4, "celery", celery_app=celery_app)
        _assert_size(4, "celery", broker_url=broker_url)


def test_a_real_inspect_call_waits_only_while_its_budget_lasts(celery_app, monkeypatch):
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_INSPECT_TIMEOUT", 1.0)
    monkeypatch.setattr(sys.modules[__name__], "_SIZE_WAIT_S", 30.0)
    for _ in range(3):
        celery_app.send_task("hirefire.test.hold", queue="celery")

    with _worker(celery_app.conf.broker_url):
        _assert_size(3, "celery", celery_app=celery_app)

        monkeypatch.setattr(celery_macro, "_HELD_TASKS_INSPECT_BUDGET", 0.0)
        started = time.monotonic()
        celery_macro._inspect_held_tasks(celery_app, None)
        assert time.monotonic() - started < 1.0

        monkeypatch.setattr(celery_macro, "_HELD_TASKS_INSPECT_BUDGET", 2.0)
        started = time.monotonic()
        assert celery_macro._inspect_held_tasks(celery_app, None) == {"celery": 3}
        assert time.monotonic() - started >= 1.5


def _clear_broker_env(monkeypatch):
    for key in (
        "HIREFIRE_CELERY_BROKER_URL",
        "CELERY_BROKER_URL",
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
        monkeypatch.delenv(key, raising=False)


def test_resolve_broker_url_multi_key_precedence(monkeypatch):
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
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://celery-broker/0")
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "redis://hirefire/0")
    assert _resolve_broker_url(None) == "redis://celery-broker/0"

    monkeypatch.delenv("CELERY_BROKER_URL")
    assert _resolve_broker_url(None) == "amqp://amqp-url"

    monkeypatch.delenv("AMQP_URL")
    assert _resolve_broker_url(None) == "amqp://rabbitmq-url"


def test_without_a_url_in_the_environment_the_local_rabbitmq_is_read(monkeypatch):
    _clear_broker_env(monkeypatch)

    assert _resolve_broker_url(None) == "amqp://guest:guest@localhost:5672"
    assert _resolve_broker_url("") == "amqp://guest:guest@localhost:5672"


@pytest.mark.parametrize(
    "url, options",
    [
        ("redis://host/0", {"socket_timeout", "socket_connect_timeout"}),
        ("rediss://host/0", {"socket_timeout", "socket_connect_timeout"}),
        ("amqp://host", {"read_timeout", "write_timeout"}),
        ("amqps://host", {"read_timeout", "write_timeout"}),
        ("pyamqp://host", {"read_timeout", "write_timeout"}),
        (
            "sqs://host",
            {
                "socket_timeout",
                "socket_connect_timeout",
                "read_timeout",
                "write_timeout",
            },
        ),
    ],
)
def test_a_sample_connection_times_out_after_five_seconds_on_every_transport(
    url, options
):
    assert celery_macro._sample_transport_options(url) == dict.fromkeys(options, 5.0)


def test_resolve_broker_url_explicit_wins_over_env(monkeypatch):
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://from-env/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://celery-broker/0")
    assert _resolve_broker_url("redis://explicit/0") == "redis://explicit/0"


DEAD_BROKER_URL = "redis://127.0.0.1:1/0"


def test_an_explicit_broker_url_wins_over_celery_broker_url(celery_app, monkeypatch):
    broker_url = celery_app.conf.broker_url
    celery_app.send_task("test_task", queue="celery")
    monkeypatch.setenv("CELERY_BROKER_URL", DEAD_BROKER_URL)

    _assert_size(1, "celery", broker_url=broker_url, skip_working=True)
    _assert_float_seconds(job_queue_latency("celery", broker_url=broker_url))
    _assert_size(1, "celery", broker_url=broker_url)
    assert job_queue_working("celery", broker_url=broker_url) == 0


def test_celery_broker_url_wins_over_the_fallback_list(celery_app, monkeypatch):
    broker_url = celery_app.conf.broker_url
    celery_app.send_task("test_task", queue="celery")
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("AMQP_URL", "amqp://guest:guest@127.0.0.1:1")
    monkeypatch.setenv("REDIS_URL", DEAD_BROKER_URL)
    monkeypatch.setenv("CELERY_BROKER_URL", broker_url)

    _assert_size(1, "celery", skip_working=True)
    _assert_float_seconds(job_queue_latency("celery"))


def test_a_plan_broker_url_wins_over_celery_broker_url(celery_app, monkeypatch):
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", celery_app.conf.broker_url)
    celery_app.send_task("test_task", queue="celery")
    monkeypatch.setenv("CELERY_BROKER_URL", DEAD_BROKER_URL)
    entry = {
        "name": "worker",
        "adapter": "celery",
        "strategy": "jqs",
        "queues": ["celery"],
        "options": {"skip_working": True},
    }

    HireFire.configuration.buffer.flush()
    sample_plan(entry)

    flushed = HireFire.configuration.buffer.flush()
    assert list(flushed["worker"]["jqs"].values())[-1] == 1


def test_hirefire_celery_broker_url_is_plan_only(monkeypatch):
    import hirefire_resource.macro.celery as celery_macro

    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "redis://hf/0")
    monkeypatch.setenv("REDIS_URL", "redis://local/0")
    assert _resolve_broker_url(None) == "redis://local/0"
    assert celery_macro.plan_connection_options() == {"broker_url": "redis://hf/0"}


def test_every_name_that_the_macro_declares_public_exists():
    assert missing_public_names(celery_macro) == []


def test_the_limits_of_a_sample_have_their_documented_values(monkeypatch):
    monkeypatch.undo()

    assert {
        "_SAMPLE_BROKER_TIMEOUT": celery_macro._SAMPLE_BROKER_TIMEOUT,
        "_HELD_TASKS_INSPECT_TIMEOUT": celery_macro._HELD_TASKS_INSPECT_TIMEOUT,
        "_HELD_TASKS_INSPECT_BUDGET": celery_macro._HELD_TASKS_INSPECT_BUDGET,
        "_HELD_TASKS_REFRESH_INTERVAL": celery_macro._HELD_TASKS_REFRESH_INTERVAL,
        "_HELD_TASKS_MAX_AGE": celery_macro._HELD_TASKS_MAX_AGE,
        "_HELD_TASKS_IDLE_TIMEOUT": celery_macro._HELD_TASKS_IDLE_TIMEOUT,
    } == {
        "_SAMPLE_BROKER_TIMEOUT": 5.0,
        "_HELD_TASKS_INSPECT_TIMEOUT": 1.0,
        "_HELD_TASKS_INSPECT_BUDGET": 2.0,
        "_HELD_TASKS_REFRESH_INTERVAL": 5.0,
        "_HELD_TASKS_MAX_AGE": 30.0,
        "_HELD_TASKS_IDLE_TIMEOUT": 60.0,
    }


def test_the_name_that_1_x_exported_for_the_amqp_library_is_kept():
    assert celery_macro.AMQP_AVAILABLE is True


def test_the_app_a_sample_makes_for_itself_neither_pools_nor_retries():
    app = celery_macro._owned_celery_app(redis_url)

    assert app.conf.broker_pool_limit is None
    assert app.conf.broker_connection_timeout == 5.0
    assert app.conf.broker_connection_retry is False
    assert app.conf.broker_connection_retry_on_startup is False
    assert app.conf.broker_connection_max_retries == 0


def test_a_call_is_tried_twice_without_a_delay_unless_told_otherwise(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    calls = []

    @celery_macro.mitigate_connection_reset_error()
    def always_reset():
        calls.append(True)
        raise ConnectionResetError("reset")

    with pytest.raises(ConnectionResetError):
        always_reset()

    assert len(calls) == 2
    assert slept == []


def test_job_queue_latency_rabbitmq_future_run_at_is_float_zero():
    class Message:
        headers = {
            "run_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        }
        delivery_tag = 7

    class FakeChannel:
        def basic_get(self, queue):
            return Message()

        def send_method(self, method, format, arguments):
            pass

    latency = _job_queue_latency_rabbitmq(FakeChannel(), "celery")

    _assert_float_seconds(latency)
    assert latency == 0


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(celery_macro, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_the_held_task_thread_waits_out_its_interval_and_ends_after_a_minute_without_a_sample(
    monkeypatch, clock
):
    waits = []

    class Wait:
        def wait(self, seconds):
            waits.append(seconds)
            clock[0] += seconds

    monkeypatch.setattr(celery_macro, "_HELD_TASKS_REFRESH_INTERVAL", 5.0)
    monkeypatch.setattr(
        celery_macro,
        "threading",
        SimpleNamespace(Event=Wait, Thread=threading.Thread, Lock=threading.Lock),
    )
    held = celery_macro._HeldTasks("key", None, None)
    celery_macro._held_tasks["key"] = held
    seconds_per_refresh = iter([3, 7, 48])
    monkeypatch.setattr(
        held,
        "_refresh",
        lambda: clock.__setitem__(0, clock[0] + next(seconds_per_refresh)),
    )

    held._run()

    assert waits == [2.0, 0.0, 0.0]
    assert clock[0] == 1060.0
    assert "key" not in celery_macro._held_tasks


def test_a_count_is_used_for_thirty_seconds_and_not_after(clock):
    held = celery_macro._HeldTasks("key", None, None)
    held._counts = {"celery": 2}
    held._counted_at = 1000.0

    clock[0] = 1030.0
    assert held.count({"celery"}) == 2

    clock[0] = 1030.5
    with pytest.raises(SampleIncompleteError):
        held.count({"celery"})


def test_a_first_count_is_waited_for_thirty_seconds_and_not_after(clock):
    held = celery_macro._HeldTasks("key", None, None)

    clock[0] = 1030.0
    with pytest.raises(SampleNotReadyError):
        held.count({"celery"})

    clock[0] = 1030.5
    with pytest.raises(SampleIncompleteError):
        held.count({"celery"})


def test_a_reply_that_is_not_a_list_does_not_hide_the_tasks_of_the_next_worker(
    celery_app, monkeypatch
):
    task = {"delivery_info": {"routing_key": "celery"}, "id": "active-1"}

    class Inspect:
        def active(self):
            return {"first@host": "error", "second@host": [task]}

        def reserved(self):
            return None

    control = SimpleNamespace(inspect=lambda **options: Inspect())
    monkeypatch.setattr(celery_app, "control", control)

    assert celery_macro._inspect_held_tasks(celery_app, None) == {"celery": 1}


def test_a_drain_at_the_end_of_the_budget_times_out(clock):
    drained = []
    connection = SimpleNamespace(drain_events=lambda **options: drained.append(options))
    budgeted = celery_macro._BudgetedConnection(connection)

    def call():
        budgeted.drain_events(timeout=1)
        clock[0] += 2.0
        with pytest.raises(socket.timeout):
            budgeted.drain_events(timeout=1)

    budgeted.within_budget(call)

    assert drained == [{"timeout": 1}]


def test_job_queue_latency_reads_through_a_celery_app(celery_app):
    enqueued_at = enqueue_for_job_queue_latency_with_job(celery_app)

    _assert_latency(job_queue_latency("celery", celery_app=celery_app), 4, enqueued_at)
    _assert_latency(
        job_queue_latency("celery", "mailer", celery_app=celery_app), 8, enqueued_at
    )


@pytest.mark.asyncio
async def test_async_job_queue_latency_reads_through_a_celery_app(celery_app):
    enqueued_at = enqueue_for_job_queue_latency_with_job(celery_app)

    _assert_latency(
        await async_job_queue_latency("mailer", celery_app=celery_app), 8, enqueued_at
    )


def test_job_queue_latency_takes_a_broker_url_or_a_celery_app_and_not_both(celery_app):
    with pytest.raises(ValueError, match="Cannot specify both"):
        job_queue_latency(
            "celery", broker_url=celery_app.conf.broker_url, celery_app=celery_app
        )
