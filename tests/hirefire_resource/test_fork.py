import logging
import os
from unittest.mock import patch

import pytest

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


def _materialize():
    config = HireFire.configuration
    config.buffer
    dispatcher = config.dispatcher
    return config, dispatcher


def test_after_fork_in_child_replaces_locks_and_drops_connections():
    config, dispatcher = _materialize()
    client = dispatcher._client
    lease_client = dispatcher._lease._client

    locks_before = [
        config._mutex,
        config.buffer._mutex,
        dispatcher._mutex,
        client._mutex,
        lease_client._mutex,
    ]
    for lock in locks_before:
        assert lock.acquire(blocking=False)

    client._connection = object()
    client._owner_pid = os.getpid() - 1
    client._last_used_at = 123.0

    HireFire.configuration._reinit_locks_after_fork()

    locks_after = [
        config._mutex,
        config.buffer._mutex,
        dispatcher._mutex,
        client._mutex,
        lease_client._mutex,
    ]
    for before, after in zip(locks_before, locks_after):
        assert after is not before
        assert after.acquire(blocking=False)
        after.release()

    assert client._connection is None
    assert client._owner_pid is None
    assert client._last_used_at is None


def test_after_fork_in_child_job_only_abandons_and_clears_buffer():
    config, dispatcher = _materialize()
    buffer = config.buffer
    buffer.sample("web", "rqt", 7)
    buffer.sample("worker", "jql", 5)
    lease = dispatcher._lease

    original_process_id = lease.process_id
    lease._granted = True
    lease._expires_at = float("inf")
    lease._next_sample_at = float("inf")

    HireFire.after_fork_in_child()
    assert config.buffer.flush() == {}

    assert lease.process_id != original_process_id
    assert lease._granted is False
    assert lease._expires_at != float("inf")
    assert lease._next_sample_at != float("inf")


def test_handoff_without_token_is_noop(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    config, dispatcher = _materialize()
    buffer = config.buffer
    buffer.sample("web", "rqt", 9)

    with patch.object(dispatcher, "start") as mock_start:
        with patch.object(dispatcher, "abandon_inherited_state") as mock_abandon:
            HireFire.after_fork_in_child()
            mock_start.assert_not_called()
            mock_abandon.assert_not_called()

    assert buffer.flush()["web"]["rqt"]


def test_handoff_with_token_starts(monkeypatch, set_HIREFIRE_TOKEN):
    monkeypatch.setenv("DYNO", "web.1")
    config, dispatcher = _materialize()

    with patch.object(dispatcher, "start") as mock_start:
        with patch.object(dispatcher, "ensure_job_queue_loop") as mock_ensure:
            HireFire.after_fork_in_child()
            mock_start.assert_called_once()
            mock_ensure.assert_called_once()


def test_after_fork_in_child_does_not_import_celery_macro(monkeypatch):
    import sys

    monkeypatch.setenv("HIREFIRE_TOKEN", "tok")
    HireFire.reset()
    # Isolate from other tests that may have imported the Celery macro.
    celery_macro = sys.modules.pop("hirefire_resource.macro.celery", None)
    try:
        assert "hirefire_resource.macro.celery" not in sys.modules
        had_celery = "celery" in sys.modules
        monkeypatch.setattr(
            HireFire.configuration, "prefork_web_handoff", lambda: False
        )
        HireFire.after_fork_in_child()
        assert "hirefire_resource.macro.celery" not in sys.modules
        if not had_celery:
            assert "celery" not in sys.modules
    finally:
        if celery_macro is not None:
            sys.modules["hirefire_resource.macro.celery"] = celery_macro


def test_after_fork_in_parent_stops_without_flush_for_web(
    monkeypatch, set_HIREFIRE_TOKEN
):
    monkeypatch.setenv("DYNO", "web.1")
    config = HireFire.configuration
    with patch.object(config, "stop_dispatcher") as mock_stop:
        HireFire.after_fork_in_parent()
        mock_stop.assert_called_once_with(flush=False)


def test_after_fork_in_parent_noop_for_job_only(monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    config = HireFire.configuration
    with patch.object(config, "stop_dispatcher") as mock_stop:
        HireFire.after_fork_in_parent()
        mock_stop.assert_not_called()


def test_the_fork_hook_swallows_reinit_failures():
    with patch.object(
        HireFire.configuration,
        "_reinit_locks_after_fork",
        side_effect=RuntimeError("boom"),
    ):
        HireFire.after_fork_in_child()


def test_after_fork_in_child_logs_start_failure(
    monkeypatch, set_HIREFIRE_TOKEN, caplog
):
    monkeypatch.setenv("DYNO", "web.1")
    caplog.set_level(logging.ERROR)
    with patch.object(Dispatcher, "start", side_effect=RuntimeError("spawn failed")):
        HireFire.after_fork_in_child()
    assert "After-fork restart failed" in caplog.text
    assert "spawn failed" in caplog.text


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


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork() is POSIX-only")
@pytest.mark.skipif(
    not hasattr(os, "register_at_fork"), reason="register_at_fork unavailable"
)
def test_real_fork_restarts_child_and_stops_parent(monkeypatch, set_HIREFIRE_TOKEN):
    monkeypatch.setenv("DYNO", "web.1")
    HireFire.boot()
    assert HireFire.configuration.dispatcher.running()

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            running = HireFire.configuration.dispatcher.running()
            os.write(write_fd, b"running" if running else b"stopped")
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    try:
        status = os.read(read_fd, 64).decode()
        _, child_status = os.waitpid(pid, 0)
    finally:
        os.close(read_fd)
        HireFire.reset()

    assert os.WIFEXITED(child_status)
    assert os.WEXITSTATUS(child_status) == 0
    assert status == "running"
    assert not HireFire.configuration.dispatcher.running()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork() is POSIX-only")
@pytest.mark.skipif(
    not hasattr(os, "register_at_fork"), reason="register_at_fork unavailable"
)
def test_real_fork_keeps_job_only_parent_running(monkeypatch, set_HIREFIRE_TOKEN):
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.boot()
    assert HireFire.configuration.dispatcher.running()
    HireFire.configuration.buffer.sample("worker", "jql", 9)

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            dispatcher = HireFire.configuration.dispatcher
            running = dispatcher.running()
            buffer_empty = HireFire.configuration.buffer.flush() == {}
            dispatcher.stop()
            parts = [
                "running" if running else "stopped",
                "empty" if buffer_empty else "full",
            ]
            os.write(write_fd, ",".join(parts).encode())
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    try:
        status = os.read(read_fd, 64).decode()
        _, child_status = os.waitpid(pid, 0)
    finally:
        os.close(read_fd)

    assert os.WIFEXITED(child_status)
    assert os.WEXITSTATUS(child_status) == 0
    assert status == "stopped,empty"
    assert HireFire.configuration.dispatcher.running()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork() is POSIX-only")
def test_forked_child_recovers_a_lock_held_across_the_fork():
    buffer = HireFire.configuration.buffer

    buffer._mutex.acquire()
    try:
        pid = os.fork()
    except OSError:
        buffer._mutex.release()
        pytest.skip("fork() unavailable")

    if pid == 0:
        code = 0
        try:
            HireFire.after_fork_in_child()
            if HireFire.configuration.buffer._mutex.acquire(timeout=5):
                HireFire.configuration.buffer._mutex.release()
            else:
                code = 1
        except BaseException:
            code = 2
        os._exit(code)

    try:
        _, status = os.waitpid(pid, 0)
    finally:
        buffer._mutex.release()

    assert os.WIFEXITED(status)
    assert os.WEXITSTATUS(status) == 0
