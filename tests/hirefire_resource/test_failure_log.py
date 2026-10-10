import logging

import pytest
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.failure_log import FailureLog
from tests.helpers import at


@pytest.fixture
def failures():
    return FailureLog("Dispatch", HireFire.configuration)


def lines(caplog):
    return [(record.levelno, record.getMessage()) for record in caplog.records]


def test_the_first_failure_is_logged_with_its_error(failures, caplog):
    failures.failed(RuntimeError("redis://user:secret@host/0 refused"))

    assert failures.count == 1
    assert lines(caplog) == [
        (
            logging.ERROR,
            "[HireFire] Dispatch error: RuntimeError: redis://***@host/0 refused",
        )
    ]


def test_failures_within_a_minute_of_a_logged_line_are_counted_and_not_logged(
    failures, caplog
):
    assert FailureLog.INTERVAL == 60
    with freeze_time(at(1000)) as clock:
        failures.failed(RuntimeError("down"))
        clock.move_to(at(1059))
        failures.failed(RuntimeError("down"))

    assert failures.count == 2
    assert len(lines(caplog)) == 1


def test_a_failure_a_minute_after_the_last_line_is_logged_with_the_count(
    failures, caplog
):
    with freeze_time(at(1000)) as clock:
        failures.failed(RuntimeError("down"))
        clock.move_to(at(1030))
        failures.failed(RuntimeError("down"))
        clock.move_to(at(1060))
        failures.failed(RuntimeError("still down"))
        clock.move_to(at(1119))
        failures.failed(RuntimeError("still down"))
        clock.move_to(at(1120))
        failures.failed(RuntimeError("down again"))

    assert [message for _level, message in lines(caplog)] == [
        "[HireFire] Dispatch error: RuntimeError: down",
        "[HireFire] Dispatch error: RuntimeError: still down "
        "(3 failed attempts in a row)",
        "[HireFire] Dispatch error: RuntimeError: down again "
        "(5 failed attempts in a row)",
    ]


def test_a_recovery_after_two_or_more_failures_is_logged_once(failures, caplog):
    caplog.set_level(logging.INFO)
    failures.failed(RuntimeError("down"))
    failures.failed(RuntimeError("down"))

    failures.recovered()
    failures.recovered()

    assert failures.count == 0
    assert lines(caplog)[1:] == [
        (logging.INFO, "[HireFire] Dispatch recovered after 2 failed attempts.")
    ]


def test_a_recovery_after_one_failure_is_not_logged(failures, caplog):
    caplog.set_level(logging.INFO)
    failures.failed(RuntimeError("down"))

    failures.recovered()

    assert failures.count == 0
    assert len(lines(caplog)) == 1


def test_the_first_failure_after_a_recovery_is_logged_at_once(failures, caplog):
    with freeze_time(at(1000)):
        failures.failed(RuntimeError("down"))
        failures.recovered()
        failures.failed(RuntimeError("down again"))

    assert [message for _level, message in lines(caplog)] == [
        "[HireFire] Dispatch error: RuntimeError: down",
        "[HireFire] Dispatch error: RuntimeError: down again",
    ]


def test_an_event_that_is_not_an_error_is_recorded_with_its_own_text(failures, caplog):
    failures.record("Dropped metrics payload: 140000 bytes.")

    assert lines(caplog) == [
        (logging.ERROR, "[HireFire] Dropped metrics payload: 140000 bytes.")
    ]


def test_the_time_between_lines_follows_the_monotonic_clock(failures, caplog, mocker):
    clock = mocker.patch("time.monotonic", return_value=5000.0)
    failures.failed(RuntimeError("down"))
    clock.return_value = 5059.9
    failures.failed(RuntimeError("down"))
    clock.return_value = 5060.0
    failures.failed(RuntimeError("down"))

    assert len(lines(caplog)) == 2


def test_the_second_logged_line_names_the_failures_in_a_row(failures, caplog):
    with freeze_time(at(1000)) as clock:
        failures.failed(RuntimeError("down"))
        clock.move_to(at(1060))
        failures.failed(RuntimeError("down"))

    assert lines(caplog)[1] == (
        logging.ERROR,
        "[HireFire] Dispatch error: RuntimeError: down (2 failed attempts in a row)",
    )
