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
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


def recent_request_start():
    return str(int(time.time() * 1000) - 5)


# The request hot path must fail open — a failure collecting the sample is logged
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


# The X-Request-Start header is client-controlled bytes; a non-UTF-8 value must
# decode leniently rather than raise UnicodeDecodeError in the request path.
def test_request_start_from_scope_handles_non_utf8_bytes():
    scope = {"headers": [(b"x-request-start", b"t=\xff\xfe")]}

    value = request_start_from_scope(scope)  # must not raise

    assert value is not None
    assert calculate_request_queue_time(value) is None  # unparseable => no sample
