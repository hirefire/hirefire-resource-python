import logging
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from freezegun import freeze_time
from kombu.exceptions import OperationalError

from hirefire_resource import plan
from hirefire_resource.errors import (
    MissingQueueError,
    SampleIncompleteError,
    SampleNotReadyError,
)
from hirefire_resource.macro import celery as celery_macro
from hirefire_resource.macro.celery import (
    _held_task_count,
    _HeldTasks,
    _inspect_held_tasks,
    async_job_queue_working,
    job_queue_working,
)

_WAIT_S = 5.0
_POLL_S = 0.005


def _task(queue, **extra):
    return {"id": "task", "delivery_info": {"routing_key": queue}, **extra}


def _scheduled(queue, eta):
    return {"eta": eta, "priority": 6, "request": _task(queue)}


class FakeInspect:
    def __init__(self, active=None, reserved=None, scheduled=None):
        self._replies = {
            "active": active,
            "reserved": reserved,
            "scheduled": scheduled,
        }

    def active(self):
        return self._replies["active"]

    def reserved(self):
        return self._replies["reserved"]

    def scheduled(self):
        return self._replies["scheduled"]


class FakeControl:
    def __init__(self, inspect=None, error=None, gate=None):
        self.inspect_calls = []
        self.started_at = []
        self.reply = inspect if inspect is not None else FakeInspect()
        self.error = error
        self.gate = gate
        self.mailbox = SimpleNamespace(producer_pool=object())

    def inspect(self, **kwargs):
        self.inspect_calls.append(kwargs)
        self.started_at.append(time.monotonic())
        if self.gate is not None:
            self.gate.wait(_WAIT_S)
        if self.error is not None:
            raise self.error
        return self.reply


class FakeConnection:
    def __init__(self):
        self.ensured = []
        self.connects = 0
        self.released = 0

    def _ensure_connection(self, **kwargs):
        self.ensured.append(kwargs)

    def connect(self):
        self.connects += 1

    def release(self):
        self.released += 1


class FakeApp:
    def __init__(self, control=None, broker_url="redis://broker/0"):
        self.control = control if control is not None else FakeControl()
        self.conf = SimpleNamespace(broker_url=broker_url)
        self.connections = []
        self.pooled = FakeConnection()

    def connection_or_acquire(self):
        return nullcontext(self.pooled)

    def connection(self):
        self.connections.append(FakeConnection())
        return self.connections[-1]


def _held_threads():
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "hirefire-celery-held-tasks"
    ]


def _wait_until(condition):
    deadline = time.monotonic() + _WAIT_S
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(_POLL_S)


def _count_when_ready(app, owned, queues):
    deadline = time.monotonic() + _WAIT_S
    while True:
        try:
            return _held_task_count(app, owned, queues)
        except SampleIncompleteError:
            assert time.monotonic() < deadline, "no counts in time"
            time.sleep(_POLL_S)


@pytest.fixture(autouse=True)
def fast_refresh(monkeypatch):
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_REFRESH_INTERVAL", 0.01)
    yield
    celery_macro.reinit_after_fork()
    for thread in _held_threads():
        thread.join(_WAIT_S)
    assert _held_threads() == []


def test_counts_active_reserved_and_due_scheduled_tasks_by_queue():
    due = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    inspect = FakeInspect(
        active={"worker-1": [_task("celery")], "worker-2": [_task("mailer")]},
        reserved={"worker-1": [_task("celery"), _task("celery")], "worker-2": []},
        scheduled={"worker-2": [_scheduled("mailer", due)]},
    )

    counts = _inspect_held_tasks(FakeApp(FakeControl(inspect)), False)

    assert counts == {"celery": 3, "mailer": 2}


def test_skips_a_scheduled_task_that_is_not_due():
    frozen = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
    inspect = FakeInspect(
        scheduled={
            "worker-1": [
                _scheduled("celery", (frozen - timedelta(seconds=1)).isoformat()),
                _scheduled("celery", frozen.isoformat()),
                _scheduled("celery", (frozen + timedelta(seconds=1)).isoformat()),
                _scheduled("celery", (frozen + timedelta(hours=1)).isoformat()),
            ]
        }
    )

    with freeze_time(frozen):
        counts = _inspect_held_tasks(FakeApp(FakeControl(inspect)), False)

    assert counts == {"celery": 2}


def test_skips_a_collection_that_no_worker_answered():
    inspect = FakeInspect(
        active={"worker-1": [_task("celery")]},
        reserved=None,
        scheduled={"worker-1": []},
    )

    assert _inspect_held_tasks(FakeApp(FakeControl(inspect)), False) == {"celery": 1}


def test_counts_nothing_when_no_worker_answers():
    assert _inspect_held_tasks(FakeApp(), False) == {}


def test_skips_a_task_it_cannot_read():
    inspect = FakeInspect(
        active={
            "worker-1": [
                _task("celery"),
                {"id": "no-delivery-info"},
                {"id": "no-routing-key", "delivery_info": {}},
                _task(None),
                "not-a-task",
            ]
        },
        reserved={"worker-1": {"error": "unknown command"}, "worker-2": None},
        scheduled={
            "worker-1": [
                _scheduled("celery", "not-a-time"),
                {"eta": "2020-01-01T00:00:00+00:00", "priority": 6},
                {"eta": None, "priority": 6, "request": _task("celery")},
            ]
        },
    )

    assert _inspect_held_tasks(FakeApp(FakeControl(inspect)), False) == {"celery": 1}


def test_inspects_a_caller_app_with_a_timeout_over_one_pooled_connection():
    app = FakeApp()

    _inspect_held_tasks(app, False)

    assert app.control.inspect_calls == [{"timeout": 1.0, "connection": app.pooled}]
    assert app.pooled.connects == 1
    assert app.connections == []


def test_inspects_an_owned_app_over_its_own_bounded_connection():
    app = FakeApp()
    _HeldTasks("redis://broker/0", app, True)

    _inspect_held_tasks(app, True)

    (connection,) = app.connections
    assert app.control.inspect_calls == [{"timeout": 1.0, "connection": connection}]
    assert connection.ensured == [
        {"max_retries": 0, "interval_start": 0, "reraise_as_library_errors": True}
    ]
    assert connection.released == 1
    assert app.control.mailbox.producer_pool is None


def test_a_caller_app_keeps_its_producer_pool():
    app = FakeApp()
    producer_pool = app.control.mailbox.producer_pool

    _HeldTasks(id(app), app, False)

    assert app.control.mailbox.producer_pool is producer_pool


def test_an_owned_connection_is_released_when_inspect_raises():
    app = FakeApp(FakeControl(error=OperationalError("broker down")))

    with pytest.raises(OperationalError):
        _inspect_held_tasks(app, True)

    assert app.connections[0].released == 1


def test_count_raises_until_a_pass_has_stored_counts():
    inspect = FakeInspect(active={"worker-1": [_task("celery"), _task("mailer")]})
    held = _HeldTasks("key", FakeApp(FakeControl(inspect)), False)

    with pytest.raises(SampleNotReadyError) as raised:
        held.count({"celery"})
    assert str(raised.value) == "Celery has not counted the tasks its workers hold yet."

    held._refresh()

    assert held.count({"celery"}) == 1
    assert held.count({"celery", "mailer"}) == 2
    assert held.count({"other"}) == 0


def test_count_raises_when_the_counts_are_older_than_30_seconds():
    held = _HeldTasks("key", FakeApp(), False)
    held._refresh()
    assert held.count({"celery"}) == 0

    held._counted_at = time.monotonic() - 29
    assert held.count({"celery"}) == 0

    held._counted_at = time.monotonic() - 31
    with pytest.raises(SampleIncompleteError) as raised:
        held.count({"celery"})
    assert not isinstance(raised.value, SampleNotReadyError)


def test_no_count_within_30_seconds_of_the_start_is_an_error():
    held = _HeldTasks("key", FakeApp(), False)

    held._started_at = time.monotonic() - 29
    with pytest.raises(SampleNotReadyError):
        held.count({"celery"})

    held._started_at = time.monotonic() - 31
    with pytest.raises(SampleIncompleteError) as raised:
        held.count({"celery"})
    assert not isinstance(raised.value, SampleNotReadyError)
    assert str(raised.value) == (
        "Celery has no recent count of the tasks its workers hold."
    )


def test_a_failed_pass_keeps_the_previous_counts():
    control = FakeControl(FakeInspect(active={"worker-1": [_task("celery")]}))
    held = _HeldTasks("key", FakeApp(control), False)
    held._refresh()
    counted_at = held._counted_at

    control.error = OperationalError("broker down")
    held._refresh()

    assert held.count({"celery"}) == 1
    assert held._counted_at == counted_at

    held._counted_at = time.monotonic() - 31
    with pytest.raises(SampleIncompleteError) as raised:
        held.count({"celery"})
    assert str(raised.value) == (
        "Celery has no recent count of the tasks its workers hold. "
        "The last refresh raised OperationalError: broker down."
    )


def test_a_failed_first_pass_reports_the_error_without_credentials():
    error = OperationalError("cannot reach amqp://user:secret@broker:5672//")
    held = _HeldTasks("key", FakeApp(FakeControl(error=error)), False)

    held._refresh()

    with pytest.raises(SampleIncompleteError) as raised:
        held.count({"celery"})
    assert not isinstance(raised.value, SampleNotReadyError)
    assert "OperationalError: cannot reach amqp://***@broker:5672//" in str(
        raised.value
    )
    assert "secret" not in str(raised.value)


def test_a_successful_pass_clears_the_error():
    control = FakeControl(error=OperationalError("broker down"))
    held = _HeldTasks("key", FakeApp(control), False)
    held._refresh()

    control.error = None
    held._refresh()
    held._counted_at = time.monotonic() - 31

    with pytest.raises(SampleIncompleteError) as raised:
        held.count({"celery"})
    assert str(raised.value) == (
        "Celery has no recent count of the tasks its workers hold."
    )


def test_the_first_call_starts_the_thread_and_has_no_count_yet():
    inspect = FakeInspect(reserved={"worker-1": [_task("celery")]})
    app = FakeApp(FakeControl(inspect))
    assert _held_threads() == []

    with pytest.raises(SampleNotReadyError):
        _held_task_count(app, False, {"celery"})

    assert len(_held_threads()) == 1
    assert _held_threads()[0].daemon
    assert _count_when_ready(app, False, {"celery"}) == 1


def _first_count_lines(caplog):
    return [
        record
        for record in caplog.records
        if "Counting the tasks Celery workers hold" in record.getMessage()
    ]


def test_the_wait_for_the_first_count_logs_one_info_line(caplog):
    caplog.set_level(logging.DEBUG)
    gate = threading.Event()
    inspect = FakeInspect(reserved={"worker-1": [_task("celery")]})
    app = FakeApp(FakeControl(inspect, gate=gate))

    for _ in range(4):
        with pytest.raises(SampleNotReadyError):
            _held_task_count(app, False, {"celery"})

    (line,) = _first_count_lines(caplog)
    assert line.levelno == logging.INFO
    assert line.getMessage() == (
        "[HireFire] Counting the tasks Celery workers hold. "
        "Samples that need the count start when the first count arrives."
    )
    assert [record for record in caplog.records if record.levelno > logging.INFO] == []

    gate.set()
    assert _count_when_ready(app, False, {"celery"}) == 1
    assert len(_first_count_lines(caplog)) == 1


def test_a_restarted_thread_logs_the_wait_again(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_IDLE_TIMEOUT", 0.1)
    app = FakeApp()

    _count_when_ready(app, False, {"celery"})
    (thread,) = _held_threads()
    thread.join(_WAIT_S)
    assert len(_first_count_lines(caplog)) == 1

    _count_when_ready(app, False, {"celery"})
    assert len(_first_count_lines(caplog)) == 2


def test_one_thread_per_caller_app():
    first, second = FakeApp(), FakeApp()

    for _ in range(3):
        _count_when_ready(first, False, {"celery"})
    assert list(celery_macro._held_tasks) == [id(first)]
    assert len(_held_threads()) == 1

    _count_when_ready(second, False, {"celery"})
    assert list(celery_macro._held_tasks) == [id(first), id(second)]
    assert len(_held_threads()) == 2


def test_one_thread_per_broker_url_for_owned_apps():
    first = FakeApp(broker_url="redis://broker/0")
    again = FakeApp(broker_url="redis://broker/0")
    other = FakeApp(broker_url="redis://broker/1")

    _count_when_ready(first, True, {"celery"})
    _count_when_ready(again, True, {"celery"})
    assert list(celery_macro._held_tasks) == ["redis://broker/0"]
    assert again.control.inspect_calls == []

    _count_when_ready(other, True, {"celery"})
    assert list(celery_macro._held_tasks) == ["redis://broker/0", "redis://broker/1"]
    assert len(_held_threads()) == 2


def test_the_next_pass_starts_one_interval_after_the_last_one_started(monkeypatch):
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_REFRESH_INTERVAL", 0.2)
    app = FakeApp()

    _count_when_ready(app, False, {"celery"})
    _wait_until(lambda: len(app.control.started_at) >= 3)

    first, second, third = app.control.started_at[:3]
    assert second - first >= 0.19
    assert third - second >= 0.19


def test_the_thread_stops_when_no_call_has_asked_for_the_idle_timeout(monkeypatch):
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_IDLE_TIMEOUT", 0.1)
    app = FakeApp()

    _count_when_ready(app, False, {"celery"})
    (thread,) = _held_threads()
    thread.join(_WAIT_S)

    assert not thread.is_alive()
    assert celery_macro._held_tasks == {}

    with pytest.raises(SampleIncompleteError):
        _held_task_count(app, False, {"celery"})
    assert len(_held_threads()) == 1
    assert _held_threads()[0] is not thread


def test_a_call_within_the_idle_timeout_keeps_the_thread(monkeypatch):
    monkeypatch.setattr(celery_macro, "_HELD_TASKS_IDLE_TIMEOUT", 0.3)
    app = FakeApp()

    _count_when_ready(app, False, {"celery"})
    (thread,) = _held_threads()
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        _held_task_count(app, False, {"celery"})
        time.sleep(0.02)

    assert thread.is_alive()
    assert _held_threads() == [thread]


def test_a_dead_thread_is_replaced():
    app = FakeApp()
    _count_when_ready(app, False, {"celery"})
    stale = _HeldTasks(id(app), app, False)
    celery_macro._held_tasks[id(app)] = stale

    with pytest.raises(SampleIncompleteError):
        _held_task_count(app, False, {"celery"})

    held = celery_macro._held_tasks[id(app)]
    assert held is not stale
    assert held.thread.is_alive()


def test_reinit_after_fork_clears_the_counts_and_replaces_the_lock():
    app = FakeApp()
    _count_when_ready(app, False, {"celery"})
    (thread,) = _held_threads()
    inherited_lock = celery_macro._held_tasks_lock

    with inherited_lock:
        celery_macro.reinit_after_fork()

    assert celery_macro._held_tasks == {}
    assert celery_macro._held_tasks_lock is not inherited_lock
    with pytest.raises(SampleIncompleteError):
        _held_task_count(app, False, {"celery"})
    thread.join(_WAIT_S)
    assert not thread.is_alive()


def test_the_plan_fork_hook_clears_the_counts():
    app = FakeApp()
    _count_when_ready(app, False, {"celery"})
    (thread,) = _held_threads()

    plan.reinit_macros_after_fork()

    assert celery_macro._held_tasks == {}
    thread.join(_WAIT_S)
    assert not thread.is_alive()


def _working_when_ready(*queues, **options):
    deadline = time.monotonic() + _WAIT_S
    while True:
        try:
            return job_queue_working(*queues, **options)
        except SampleIncompleteError:
            assert time.monotonic() < deadline, "no counts in time"
            time.sleep(_POLL_S)


def _holding_app():
    due = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return FakeApp(
        FakeControl(
            FakeInspect(
                active={"worker-1": [_task("celery")], "worker-2": [_task("mailer")]},
                reserved={"worker-1": [_task("celery"), _task("celery")]},
                scheduled={
                    "worker-2": [_scheduled("mailer", due), _scheduled("mailer", later)]
                },
            )
        )
    )


def test_job_queue_working_returns_the_stored_held_task_count():
    app = _holding_app()

    with pytest.raises(SampleNotReadyError):
        job_queue_working("celery", celery_app=app)

    working = _working_when_ready("celery", celery_app=app)
    assert type(working) is int
    assert working == 3
    assert _working_when_ready("mailer", celery_app=app) == 2
    assert _working_when_ready("celery", "mailer", celery_app=app) == 5
    assert _working_when_ready("other", celery_app=app) == 0
    assert job_queue_working("celery", celery_app=app) == _held_task_count(
        app, False, {"celery"}
    )
    assert len(_held_threads()) == 1


def test_job_queue_working_requires_queue_names():
    with pytest.raises(MissingQueueError):
        job_queue_working(celery_app=FakeApp())

    assert _held_threads() == []


def test_job_queue_working_rejects_a_celery_app_with_a_broker_url():
    with pytest.raises(ValueError, match="Cannot specify both"):
        job_queue_working("celery", celery_app=FakeApp(), broker_url="redis://broker/0")

    assert _held_threads() == []


@pytest.mark.asyncio
async def test_async_job_queue_working_returns_the_stored_held_task_count():
    app = _holding_app()
    assert _working_when_ready("celery", celery_app=app) == 3

    assert await async_job_queue_working("celery", celery_app=app) == 3
    assert await async_job_queue_working("celery", "mailer", celery_app=app) == 5
