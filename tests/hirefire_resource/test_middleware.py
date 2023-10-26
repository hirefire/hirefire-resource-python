import time

import pytest
from freezegun import freeze_time

from autoscale_agent.configuration import Configuration
from autoscale_agent.middleware import Middleware, NotConfigured, RequestInfo
from tests.helpers import TOKEN


def build_config():
    return Configuration("render")


def call(config, path="", headers={}):
    return Middleware(config).process_request(RequestInfo(path, headers))


def test_call_default():
    assert None == call(build_config())


def test_not_configured():
    with pytest.raises(NotConfigured):
        call(None)


def test_call_serve():
    config = build_config().serve(TOKEN, lambda: 1.23)
    response = call(
        config, "/autoscale", {"HTTP_AUTOSCALE_METRIC_TOKENS": f"{TOKEN},invalid"}
    )
    headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    }
    assert (200, headers, b"1.23") == response


def test_call_serve_404():
    config = build_config().serve(TOKEN, lambda: 1.23)
    response = call(config, "/autoscale", {"HTTP_AUTOSCALE_METRIC_TOKENS": "invalid"})
    assert (404, {}, "Not Found") == response


def test_call_record_queue_time_on_render():
    config = build_config().dispatch(TOKEN)
    for second, request_start in [[0, 500_000], [0, 1_000_000], [1, 1_500_000]]:
        with freeze_time(f"2000-01-01 00:00:0{second}"):
            current_time = int(time.time() * 1_000_000)
            response = call(
                config, "/", {"HTTP_X_REQUEST_START": (current_time - request_start)}
            )
            assert None == response

    buffer = config.web_dispatcher._buffer
    assert {946684800: 1000, 946684801: 1500} == buffer


def test_call_record_queue_time_missing_headers():
    config = build_config().dispatch(TOKEN)
    response = call(config, "/", {})
    assert None == response
    assert {} == config.web_dispatcher._buffer
