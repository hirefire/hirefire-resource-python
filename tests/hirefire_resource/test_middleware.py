import json
import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware import Middleware, NotConfigured, RequestInfo
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


def build_config():
    return Configuration()


def call(config, path="", headers={}):
    return Middleware(config).process_request(RequestInfo(path, headers))


def test_call_default():
    assert None == call(build_config())


def test_not_configured():
    with pytest.raises(NotConfigured):
        call(None)


def test_info_response(set_HIREFIRE_TOKEN):
    config = build_config().dyno("worker", lambda: 1.23)
    response = call(config, f"/hirefire/{HIREFIRE_TOKEN}/info")
    headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    }
    assert (200, headers, json.dumps([{"name": "worker", "value": 1.23}])) == response


def test_process_request_queue_time(set_HIREFIRE_TOKEN):
    config = build_config().dyno("web")
    with patch.object(config.web, "start") as mock_start:
        for second, request_start in [[0, 500], [0, 1_000], [1, 1_500]]:
            with freeze_time(f"2000-01-01 00:00:0{second}"):
                current_time = int(time.time() * 1_000)
                response = call(
                    config,
                    "/",
                    {"HTTP_X_REQUEST_START": (current_time - request_start)},
                )
                assert None == response
                mock_start.assert_called()
    assert {946684800: [500, 1000], 946684801: [1500]} == config.web._buffer


def test_process_request_queue_time_config(set_HIREFIRE_TOKEN):
    config = build_config()
    response = call(config, "/", {"HTTP_X_REQUEST_START": time.time()})
    assert None == response
    assert None == config.web


def test_process_request_queue_time_missing_headers(set_HIREFIRE_TOKEN):
    config = build_config().dyno("web")
    with patch.object(config.web, "start") as mock_start:
        response = call(config, "/", {})
        assert None == response
        assert {} == config.web._buffer
        mock_start.assert_not_called()
