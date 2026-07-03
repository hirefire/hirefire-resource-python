import os

import pytest

from hirefire_resource import HireFire
from hirefire_resource.hirefire import _reinit_after_fork


def _materialize():
    config = HireFire.configuration
    config.buffer
    dispatcher = config.dispatcher
    return config, dispatcher


def test_reinit_after_fork_replaces_locks_and_drops_connections():
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

    _reinit_after_fork()

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
