import logging
import time
from unittest.mock import patch

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware import (
    calculate_request_queue_time,
    process_request_queue_time,
)
from hirefire_resource.middleware.asgi import request_start_from_scope
from hirefire_resource.middleware.wsgi import request_start_from_environ
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


def recent_request_start():
    return str(int(time.time() * 1000) - 5)


def test_process_request_queue_time_swallows_metric_path_failures(
    set_HIREFIRE_TOKEN, caplog, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    caplog.set_level(logging.ERROR)
    with patch.object(Dispatcher, "start"):
        HireFire.boot()

    with patch.object(Dispatcher, "start", side_effect=RuntimeError("no threads")):
        process_request_queue_time(recent_request_start())

    assert "Middleware error" in caplog.text
    data = HireFire.configuration.buffer.flush()
    assert "web" in data and "rqt" in data["web"]


def test_process_request_queue_time_survives_a_raising_logger(
    set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start"):
        HireFire.boot()

    class RaisingLogger:
        def error(self, message):
            raise RuntimeError("logger down")

    HireFire.configuration.logger = RaisingLogger()

    with patch.object(Dispatcher, "start", side_effect=RuntimeError("no threads")):
        process_request_queue_time(recent_request_start())

    data = HireFire.configuration.buffer.flush()
    assert "web" in data and "rqt" in data["web"]


def test_process_request_queue_time_without_a_request_start_is_a_noop(
    set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start"):
        HireFire.boot()

    with patch.object(Dispatcher, "start") as start:
        process_request_queue_time(None)
        process_request_queue_time("")

    assert "web" not in HireFire.configuration.buffer.flush()
    start.assert_not_called()


def test_process_request_queue_time_ignores_an_unparseable_value(
    set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start"):
        HireFire.boot()

    with patch.object(Dispatcher, "start") as start:
        process_request_queue_time("garbage")

    assert "web" not in HireFire.configuration.buffer.flush()
    start.assert_not_called()


def test_request_start_from_scope_handles_non_utf8_bytes():
    scope = {"headers": [(b"x-request-start", b"t=\xff\xfe")]}

    value = request_start_from_scope(scope)

    assert value is not None
    assert calculate_request_queue_time(value) is None


def test_request_start_from_scope_falls_back_to_x_queue_start():
    scope = {"headers": [(b"x-queue-start", b"1700000000000")]}
    assert request_start_from_scope(scope) == "1700000000000"


def test_request_start_from_scope_blank_request_start_falls_back_to_queue_start():
    scope = {
        "headers": [
            (b"x-request-start", b"   "),
            (b"x-queue-start", b"1700000000000"),
        ]
    }
    assert request_start_from_scope(scope) == "1700000000000"


def test_request_start_from_scope_prefers_x_request_start():
    scope = {
        "headers": [
            (b"x-queue-start", b"1699999996000"),
            (b"x-request-start", b"1700000000000"),
        ]
    }
    assert request_start_from_scope(scope) == "1700000000000"


def test_process_request_queue_time_extract_failures_are_swallowed(set_HIREFIRE_TOKEN):
    def boom():
        raise AttributeError("bad headers")

    process_request_queue_time(extract=boom)


def test_request_start_from_environ_falls_back_to_x_queue_start():
    environ = {"HTTP_X_QUEUE_START": "1700000000000"}
    assert request_start_from_environ(environ) == "1700000000000"


def test_request_start_from_environ_prefers_x_request_start():
    environ = {
        "HTTP_X_REQUEST_START": "1700000000000",
        "HTTP_X_QUEUE_START": "1699999996000",
    }
    assert request_start_from_environ(environ) == "1700000000000"


def test_request_start_from_environ_blank_request_start_falls_back_to_queue_start():
    environ = {
        "HTTP_X_REQUEST_START": "   ",
        "HTTP_X_QUEUE_START": "1700000000000",
    }
    assert request_start_from_environ(environ) == "1700000000000"


def test_request_start_from_environ_whitespace_request_start_falls_back_to_queue_start():
    environ = {
        "HTTP_X_REQUEST_START": "  \t  ",
        "HTTP_X_QUEUE_START": "1700000000000",
    }
    assert request_start_from_environ(environ) == "1700000000000"


def test_calculate_request_queue_time_normalizes_every_precision_variant():
    with patch("time.time", return_value=1_700_000_001):
        assert calculate_request_queue_time("t=1700000000.250") == 750
        assert calculate_request_queue_time("t=1700000000250") == 750
        assert calculate_request_queue_time("1700000000250") == 750
        assert calculate_request_queue_time("1700000000250000") == 750
        assert calculate_request_queue_time("1700000000250000000") == 750


def test_calculate_request_queue_time_clamps_a_future_microsecond_start_to_zero():
    with patch("time.time", return_value=1_700_000_001):
        assert calculate_request_queue_time("1700000005000000") == 0


def test_calculate_request_queue_time_drops_an_over_the_limit_nanosecond_start():
    with patch("time.time", return_value=1_700_000_000):
        assert calculate_request_queue_time("1699999000000000000") is None


def test_calculate_request_queue_time_lower_guard_boundary():
    with patch("time.time", return_value=1_000_000_001):
        assert calculate_request_queue_time("1000000000") == 1000
        assert calculate_request_queue_time("999999999") is None


def test_calculate_request_queue_time_cap_boundary():
    with patch("time.time", return_value=1_700_000_000):
        assert calculate_request_queue_time("1699999940000") == 60_000
        assert calculate_request_queue_time("1699999939999") is None


def test_calculate_request_queue_time_reads_a_folded_duplicate_header():
    with patch("time.time", return_value=1_700_000_001):
        assert calculate_request_queue_time("1700000000000, 1700000000500") == 1000
        assert (
            calculate_request_queue_time("t=1700000000.000, t=1700000000.500") == 1000
        )


def test_marks_http_active_for_tokened_request_without_platform_web_role(
    set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "api")
    with patch.object(Dispatcher, "start"), patch.object(
        Dispatcher, "ensure_job_queue_loop"
    ):
        HireFire.boot()

    assert not HireFire.configuration.rqt_enabled()
    with patch.object(Dispatcher, "start"), patch.object(
        Dispatcher, "ensure_job_queue_loop"
    ):
        process_request_queue_time(str(int(time.time() * 1000) - 1000))

    assert HireFire.configuration.rqt_enabled()
    data = HireFire.configuration.buffer.flush()
    assert "api" in data and "rqt" in data["api"]


def test_does_not_sample_without_identity_or_explicit_http_name(set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start"), patch.object(
        Dispatcher, "ensure_job_queue_loop"
    ):
        process_request_queue_time(str(int(time.time() * 1000) - 5))
    assert HireFire.configuration.buffer.flush() == {}


def test_does_not_mark_http_active_without_token(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "api")
    HireFire.boot()
    process_request_queue_time(str(int(time.time() * 1000) - 5))
    assert not HireFire.configuration.rqt_enabled()
    assert HireFire.configuration.buffer.flush() == {}


def test_calculate_request_queue_time_ignores_non_finite_headers():
    assert calculate_request_queue_time("NaN") is None
    assert calculate_request_queue_time("Infinity") is None
    assert calculate_request_queue_time("t=Infinity") is None
    assert calculate_request_queue_time("1e500") is None


def test_calculate_request_queue_time_rounds_a_fractional_millisecond_remainder():
    with patch("time.time", return_value=1_700_000_001):
        assert calculate_request_queue_time("t=1700000000.2506") == 749


def test_calculate_request_queue_time_rounds_a_fractional_nanosecond_remainder():
    with patch("time.time", return_value=1_700_000_001):
        assert calculate_request_queue_time("1700000000250600000") == 749


def test_calculate_request_queue_time_drops_a_negative_request_start():
    assert calculate_request_queue_time("-1700000000250") is None
