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


# The request hot path must fail open: a failure collecting the sample is logged
# and swallowed, never raised into the host application's request.
def test_process_request_queue_time_swallows_metric_path_failures(
    set_HIREFIRE_TOKEN, caplog
):
    caplog.set_level(logging.ERROR)
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("web")

    with patch.object(Dispatcher, "start", side_effect=RuntimeError("no threads")):
        process_request_queue_time(recent_request_start())  # must not raise

    assert "Failed to process request queue time" in caplog.text
    # The sample is buffered before the dispatcher start, so it still lands.
    assert HireFire.configuration.buffer.flush()["web"]


# The X-Request-Start header is client-controlled bytes. A non-UTF-8 value must
# decode leniently rather than raise UnicodeDecodeError in the request path.
def test_request_start_from_scope_handles_non_utf8_bytes():
    scope = {"headers": [(b"x-request-start", b"t=\xff\xfe")]}

    value = request_start_from_scope(scope)  # must not raise

    assert value is not None
    assert calculate_request_queue_time(value) is None  # unparseable => no sample


# X-Queue-Start is an exact synonym for X-Request-Start (e.g. Render emits it).
def test_request_start_from_scope_falls_back_to_x_queue_start():
    scope = {"headers": [(b"x-queue-start", b"1700000000000")]}
    assert request_start_from_scope(scope) == "1700000000000"


def test_request_start_from_scope_prefers_x_request_start():
    scope = {
        "headers": [
            (b"x-queue-start", b"1699999996000"),
            (b"x-request-start", b"1700000000000"),
        ]
    }
    assert request_start_from_scope(scope) == "1700000000000"


def test_request_start_from_environ_falls_back_to_x_queue_start():
    environ = {"HTTP_X_QUEUE_START": "1700000000000"}
    assert request_start_from_environ(environ) == "1700000000000"


def test_request_start_from_environ_prefers_x_request_start():
    environ = {
        "HTTP_X_REQUEST_START": "1700000000000",
        "HTTP_X_QUEUE_START": "1699999996000",
    }
    assert request_start_from_environ(environ) == "1700000000000"


def test_calculate_request_queue_time_keeps_a_high_but_plausible_value():
    with patch("time.time", return_value=1_700_000_000):
        # 50s: severe overload but under the limit, so still reported.
        assert calculate_request_queue_time("1699999950000") == 50_000


def test_calculate_request_queue_time_drops_an_over_the_limit_value():
    with patch("time.time", return_value=1_700_000_000):
        # ~16 min of queue time, over the 60-second cap.
        assert calculate_request_queue_time("1699999000000") is None


def test_calculate_request_queue_time_parses_each_router_unit():
    # The same instant in seconds, milliseconds, microseconds, and nanoseconds.
    with patch("time.time", return_value=1_700_000_001):
        assert calculate_request_queue_time("t=1700000000.000") == 1000
        assert calculate_request_queue_time("1700000000000") == 1000
        assert calculate_request_queue_time("1700000000000000") == 1000
        assert calculate_request_queue_time("1700000000000000000") == 1000


def test_calculate_request_queue_time_normalizes_every_precision_variant():
    # The same instant (epoch 1700000000.250) in each unit a router may emit. All
    # must normalize to the identical 750ms. The 250ms fraction exercises the
    # sub-millisecond path in every unit, including nanoseconds (whose value
    # exceeds a float's exact-integer range, so truncation would lose a ms).
    with patch("time.time", return_value=1_700_000_001):
        assert calculate_request_queue_time("t=1700000000.250") == 750  # seconds
        assert calculate_request_queue_time("1700000000250") == 750  # milliseconds
        assert calculate_request_queue_time("1700000000250000") == 750  # microseconds
        assert calculate_request_queue_time("1700000000250000000") == 750  # nanoseconds


def test_calculate_request_queue_time_clamps_a_future_microsecond_start_to_zero():
    with patch("time.time", return_value=1_700_000_001):
        # Clamp-to-zero is applied after unit inference, regardless of unit.
        assert calculate_request_queue_time("1700000005000000") == 0


def test_calculate_request_queue_time_drops_an_over_the_limit_nanosecond_start():
    with patch("time.time", return_value=1_700_000_000):
        # ~1000s in the past in nanoseconds: the 60s cap drops it regardless of unit.
        assert calculate_request_queue_time("1699999000000000000") is None


def test_calculate_request_queue_time_lower_guard_boundary():
    with patch("time.time", return_value=1_000_000_001):
        # Exactly 1e9 is a valid epoch-seconds timestamp (2001-09-09), not rejected.
        assert calculate_request_queue_time("1000000000") == 1000
        # One below the 1e9 guard is implausible and dropped.
        assert calculate_request_queue_time("999999999") is None


def test_calculate_request_queue_time_cap_boundary():
    with patch("time.time", return_value=1_700_000_000):
        # Exactly 60_000ms is at the inclusive limit, so kept. One over is dropped.
        assert calculate_request_queue_time("1699999940000") == 60_000
        assert calculate_request_queue_time("1699999939999") is None
