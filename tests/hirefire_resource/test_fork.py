import http.client
import logging
import os
import socket
import sys
import threading
import time
from unittest.mock import patch

import pytest

from hirefire_resource import HireFire
from hirefire_resource.client import Client
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.dispatcher.session import Session
from hirefire_resource.middleware import process_request_queue_time
from tests.helpers import fork_and_report, set_HIREFIRE_TOKEN  # noqa: F401

pytestmark = pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning"
)

LOCKS = {
    "configuration": lambda configuration, dispatcher: configuration._mutex,
    "once": lambda configuration, dispatcher: configuration.once._mutex,
    "buffer": lambda configuration, dispatcher: configuration.buffer._mutex,
    "dispatcher": lambda configuration, dispatcher: dispatcher._mutex,
    "session": lambda configuration, dispatcher: dispatcher._session._mutex,
    "client": lambda configuration, dispatcher: dispatcher._session._client._mutex,
    "lease": lambda configuration, dispatcher: dispatcher._session._lease._mutex,
    "lease client": (
        lambda configuration, dispatcher: dispatcher._session._lease._client._mutex
    ),
}


needs_waitid = pytest.mark.skipif(
    not hasattr(os, "waitid"), reason="os.waitid is missing on this platform"
)


def handoff_threads():
    return [
        thread for thread in threading.enumerate() if thread.name == "hirefire-handoff"
    ]


def wait_until(condition, seconds=3):
    for _ in range(int(seconds / 0.005)):
        if condition():
            return
        time.sleep(0.005)
    pytest.fail(f"the condition was not met within {seconds} seconds")


@pytest.fixture
def web_process(monkeypatch, set_HIREFIRE_TOKEN):
    monkeypatch.setenv("DYNO", "web.1")
    monkeypatch.setattr(Session, "TICK", 0.01)
    HireFire.boot()
    assert HireFire.configuration.dispatcher.running()
    return HireFire.configuration.dispatcher


@pytest.fixture
def children():
    read_fd, write_fd = os.pipe()
    pids = []

    def fork():
        pid = os.fork()
        if pid == 0:
            os.close(write_fd)
            os.read(read_fd, 1)
            os._exit(0)
        pids.append(pid)

    def release():
        os.close(write_fd)
        for pid in pids:
            os.waitpid(pid, 0)
        pids.clear()

    yield fork, release
    os.close(read_fd)
    if pids:
        release()


def connected_client():
    client = Client(HireFire.configuration)
    sock = socket.socket()
    connection = http.client.HTTPConnection("127.0.0.1")
    connection.sock = sock
    client._connection = connection
    client._last_used_at = 1.0
    return client, sock.fileno()


def test_discarding_an_inherited_connection_leaves_the_descriptor_of_the_parent_open():
    client, descriptor = connected_client()
    client._owner_pid = os.getpid()

    client.discard_inherited()

    assert client._connection is None
    os.fstat(descriptor)


def test_reset_connection_on_pid_mismatch_detaches_without_close():
    client, descriptor = connected_client()
    client._owner_pid = os.getpid() - 1

    client._reset_connection()

    assert client._connection is None
    os.fstat(descriptor)


def test_after_fork_in_child_job_only_abandons_inherited_state(set_HIREFIRE_TOKEN):
    configuration = HireFire.configuration
    configuration.buffer.sample("web", "rqt", 7)
    configuration.buffer.sample("worker", "jql", 5)

    with patch.object(Dispatcher, "start") as start:
        HireFire.after_fork_in_child()
        start.assert_not_called()

    assert configuration.buffer.flush() == {}
    assert not configuration.dispatcher.running()


def test_after_fork_in_child_web_without_token_does_not_start(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")

    with patch.object(Dispatcher, "start") as start:
        HireFire.after_fork_in_child()
        start.assert_not_called()


def test_after_fork_in_child_starts_when_token_present(monkeypatch, set_HIREFIRE_TOKEN):
    monkeypatch.setenv("DYNO", "web.1")

    with patch.object(Dispatcher, "start") as start:
        HireFire.after_fork_in_child()
        start.assert_called_once()


def test_after_fork_in_child_does_not_import_celery_macro(monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "tok")
    HireFire.reset()
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
        assert "hirefire_resource.macro.celery" not in sys.modules
        assert "celery" not in sys.modules
        monkeypatch.setattr(
            HireFire.configuration, "prefork_web_handoff", lambda: False
        )
        HireFire.after_fork_in_child()
        assert "hirefire_resource.macro.celery" not in sys.modules
        assert "celery" not in sys.modules
    finally:
        sys.modules.update(removed)


def test_after_fork_in_parent_stops_without_flush(monkeypatch, set_HIREFIRE_TOKEN):
    monkeypatch.setenv("DYNO", "web.1")
    monkeypatch.delattr(os, "waitid", raising=False)
    config = HireFire.configuration
    with patch.object(config, "stop_dispatcher") as mock_stop:
        HireFire.after_fork_in_parent()
        mock_stop.assert_called_once_with(flush=False)

    assert handoff_threads() == []


def test_after_fork_in_parent_is_noop_for_job_only_process(monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    config = HireFire.configuration
    with patch.object(config, "stop_dispatcher") as mock_stop:
        HireFire.after_fork_in_parent()
        mock_stop.assert_not_called()


def test_the_fork_hook_logs_a_failure_and_does_not_raise(caplog):
    with patch.object(
        HireFire.configuration, "unlock_after_fork", side_effect=RuntimeError("boom")
    ):
        HireFire.after_fork_in_child()

    assert "[HireFire] After-fork restart failed: boom" in caplog.text


def test_after_fork_in_child_logs_start_failure(
    monkeypatch, set_HIREFIRE_TOKEN, caplog
):
    monkeypatch.setenv("DYNO", "web.1")
    caplog.set_level(logging.ERROR)
    with patch.object(Dispatcher, "start", side_effect=RuntimeError("spawn failed")):
        HireFire.after_fork_in_child()
    assert "After-fork restart failed" in caplog.text
    assert "spawn failed" in caplog.text


def test_after_fork_in_parent_is_noop_for_a_web_process_that_has_served_a_request(
    monkeypatch,
):
    monkeypatch.setenv("DYNO", "web.1")
    config = HireFire.configuration
    config.mark_http_active()
    with patch.object(config, "stop_dispatcher") as mock_stop:
        HireFire.after_fork_in_parent()
        mock_stop.assert_not_called()

    assert handoff_threads() == []


def test_after_fork_in_parent_logs_stop_failure(monkeypatch, caplog):
    monkeypatch.setenv("DYNO", "web.1")
    caplog.set_level(logging.ERROR)
    config = HireFire.configuration
    with patch.object(
        config, "stop_dispatcher", side_effect=RuntimeError("stop failed")
    ):
        HireFire.after_fork_in_parent()
    assert "After-fork parent stop failed" in caplog.text
    assert "stop failed" in caplog.text


def test_install_fork_hooks_is_idempotent():
    HireFire.install_fork_hooks()
    HireFire.install_fork_hooks()
    assert HireFire._fork_hooks_installed


def test_real_fork_restarts_child_and_stops_parent(monkeypatch, set_HIREFIRE_TOKEN):
    assert Session.PREFORK_WAIT == 1
    monkeypatch.setenv("DYNO", "web.1")
    HireFire.boot()
    assert HireFire.configuration.dispatcher.running()

    report = fork_and_report(HireFire.configuration.dispatcher.running)

    assert report == "True"
    assert not HireFire.configuration.dispatcher.running()


def test_real_fork_keeps_job_only_parent_running(monkeypatch, set_HIREFIRE_TOKEN):
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.boot()
    assert HireFire.configuration.dispatcher.running()
    HireFire.configuration.buffer.sample("worker", "jql", 9)

    def child():
        dispatcher = HireFire.configuration.dispatcher
        return [
            dispatcher.running(),
            HireFire.configuration.buffer.flush(),
            dispatcher.stop(),
        ]

    assert fork_and_report(child) == "[False, {}, False]"
    assert HireFire.configuration.dispatcher.running()


@pytest.mark.parametrize("name", LOCKS)
def test_a_forked_child_starts_although_another_thread_held_a_lock_at_the_fork(
    name, monkeypatch, set_HIREFIRE_TOKEN
):
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.boot()
    configuration = HireFire.configuration
    lock = LOCKS[name](configuration, configuration.dispatcher)
    held = threading.Event()
    release = threading.Event()

    def hold():
        with lock:
            held.set()
            release.wait(10)

    def child():
        configuration.dyno("web")
        configuration.buffer.sample("worker", "jql", 1)
        dispatcher = configuration.dispatcher
        return [dispatcher.start(), dispatcher.running(), dispatcher.stop(flush=False)]

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(5)
    try:
        report = fork_and_report(child)
    finally:
        release.set()
        holder.join(5)

    assert report == "[True, True, True]"


def test_a_child_forked_while_its_parent_stops_starts_its_dispatcher(
    monkeypatch, set_HIREFIRE_TOKEN
):
    monkeypatch.setenv("DYNO", "web.1")
    HireFire.boot()
    dispatcher = HireFire.configuration.dispatcher
    in_flush = threading.Event()
    release = threading.Event()

    def flush(_session):
        in_flush.set()
        release.wait(10)

    monkeypatch.setattr(Session, "flush", flush)
    stopper = threading.Thread(target=dispatcher.stop)
    stopper.start()
    assert in_flush.wait(5)
    try:
        report = fork_and_report(HireFire.configuration.dispatcher.running)
    finally:
        release.set()
        stopper.join(5)

    assert report == "True"


@needs_waitid
def test_a_web_process_that_forks_a_helper_reports_again_once_the_helper_is_gone(
    web_process,
):
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)

    wait_until(web_process.running)
    wait_until(lambda: not handoff_threads())


@needs_waitid
def test_a_web_process_that_handed_over_stays_stopped_while_its_child_lives(
    web_process, children
):
    fork, release = children
    fork()
    fork()
    time.sleep(0.1)
    assert not web_process.running()
    assert len(handoff_threads()) == 1

    release()

    wait_until(web_process.running)


@needs_waitid
def test_a_child_that_has_exited_and_was_not_waited_for_keeps_its_parent_stopped(
    web_process,
):
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    time.sleep(0.1)
    assert not web_process.running()

    os.waitpid(pid, 0)

    wait_until(web_process.running)


def test_a_web_process_that_has_served_a_request_keeps_reporting_when_it_forks_and_its_child_reports_nothing(
    web_process,
):
    process_request_queue_time(str(int(time.time() * 1000) - 5))

    report = fork_and_report(HireFire.configuration.dispatcher.running)

    assert report == "False"
    assert web_process.running()
    assert handoff_threads() == []


@needs_waitid
def test_a_reset_after_a_handover_is_not_undone_when_the_child_is_gone(
    web_process, caplog
):
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    HireFire.reset()
    os.waitpid(pid, 0)

    wait_until(lambda: not handoff_threads())
    assert not HireFire.configuration.dispatcher.running()
    assert not web_process.running()
    assert "After-fork resume failed" not in caplog.text


@needs_waitid
def test_a_parent_that_started_again_on_its_own_is_left_alone(web_process, children):
    fork, release = children
    fork()
    assert not web_process.running()

    assert web_process.start()

    wait_until(lambda: not handoff_threads())
    assert web_process.running()


@needs_waitid
def test_a_forked_child_does_not_keep_waiting_for_the_children_its_parent_waited_for(
    web_process, children
):
    fork, release = children
    fork()
    assert len(handoff_threads()) == 1

    def child():
        waiting = HireFire._handed_over
        HireFire.after_fork_in_parent()
        return [waiting, len(handoff_threads())]

    assert fork_and_report(child) == "[False, 1]"


def test_a_failure_while_a_parent_waits_for_its_children_is_logged_and_a_later_fork_is_watched_again(
    monkeypatch, set_HIREFIRE_TOKEN, caplog
):
    monkeypatch.setenv("DYNO", "web.1")
    monkeypatch.setattr(Session, "TICK", 0.01)

    def boom(*_arguments):
        raise RuntimeError("waitid boom")

    def no_children(*_arguments):
        raise ChildProcessError()

    monkeypatch.setattr(os, "waitid", boom, raising=False)
    monkeypatch.setattr(os, "P_ALL", 0, raising=False)
    monkeypatch.setattr(os, "WEXITED", 4, raising=False)
    monkeypatch.setattr(os, "WNOWAIT", 32, raising=False)
    HireFire.after_fork_in_parent()
    wait_until(
        lambda: "[HireFire] After-fork resume failed: waitid boom" in caplog.text
    )
    wait_until(lambda: not handoff_threads())

    monkeypatch.setattr(os, "waitid", no_children, raising=False)
    with patch.object(Dispatcher, "start") as start:
        HireFire.after_fork_in_parent()
        wait_until(lambda: not handoff_threads())
        start.assert_called_once()
