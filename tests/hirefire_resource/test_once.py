import logging
import threading

import pytest

from hirefire_resource import HireFire
from hirefire_resource.once import Once


@pytest.fixture
def once():
    return Once(HireFire.configuration)


def lines(caplog):
    return [record.getMessage() for record in caplog.records]


def test_a_line_is_logged_the_first_time_and_not_again(once, caplog):
    for _ in range(3):
        once.log("warning", "cpu_unresolved", lambda: "no identity")

    assert lines(caplog) == ["no identity"]
    assert caplog.records[0].levelno == logging.WARNING


def test_the_message_is_not_built_for_a_line_that_was_logged_before(once):
    built = []
    for _ in range(2):
        once.log("error", "problem", lambda: str(built.append(1)), key="worker")

    assert built == [1]


def test_each_key_of_a_kind_and_each_kind_gets_its_own_line(once, caplog):
    once.log("error", "unknown_adapter", lambda: "first", key="worker")
    once.log("error", "unknown_adapter", lambda: "second", key="mailer")
    once.log("error", "unloaded_adapter", lambda: "third", key="worker")
    once.log("error", "unknown_adapter", lambda: "fourth", key=("worker", "rq"))
    once.log("error", "unknown_adapter", lambda: "fifth", key="worker")

    assert lines(caplog) == ["first", "second", "third", "fourth"]


def test_the_line_is_logged_at_the_level_given(once, caplog):
    once.log("info", "a", lambda: "informed")
    once.log("error", "b", lambda: "failed")

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.INFO, "informed"),
        (logging.ERROR, "failed"),
    ]


def test_a_kind_remembers_its_last_256_keys(once, caplog):
    assert Once.LIMIT == 256
    for index in range(1, Once.LIMIT + 1):
        once.log("warning", "queue", lambda: "line", key=index)
    once.log("warning", "queue", lambda: "line", key=1)
    assert len(lines(caplog)) == Once.LIMIT

    once.log("warning", "queue", lambda: "line", key=Once.LIMIT + 1)
    once.log("warning", "queue", lambda: "line", key=2)
    once.log("warning", "queue", lambda: "line", key=1)
    assert len(lines(caplog)) == Once.LIMIT + 2


def test_threads_that_arrive_together_log_one_line(once, caplog):
    gate = threading.Event()

    def arrive():
        gate.wait()
        once.log("warning", "together", lambda: "together")

    threads = [threading.Thread(target=arrive) for _ in range(8)]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join()

    assert lines(caplog) == ["together"]


def test_the_logger_in_use_when_the_line_is_due_receives_it(once, caplog):
    received = []

    class Later:
        def warning(self, message):
            received.append(message)

    HireFire.configuration.logger = Later()

    once.log("warning", "swapped", lambda: "to the new logger")

    assert lines(caplog) == []
    assert received == ["to the new logger"]


def test_a_logger_that_raises_does_not_raise_into_the_caller(once):
    class Broken:
        def warning(self, message):
            raise OSError("closed stream")

    HireFire.configuration.logger = Broken()

    once.log("warning", "broken", lambda: "lost")


def test_a_lock_that_was_held_at_a_fork_is_replaced_in_the_child(once, caplog):
    once.log("warning", "before", lambda: "before")
    once._mutex.acquire()

    once.unlock_after_fork()
    once.log("warning", "before", lambda: "before")
    once.log("warning", "after", lambda: "after")

    assert lines(caplog) == ["before", "after"]
