import json
import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware import NotConfigured, RequestInfo
from hirefire_resource.middleware.wsgi import BaseMiddleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


def call(config, path="", headers={}):
    return BaseMiddleware(config).process_request(RequestInfo(path, headers))


def worker_data():
    return 1.23


def test_pass():
    assert None == call(Configuration())


def test_not_configured_error():
    with pytest.raises(NotConfigured):
        call(None)


def test_web(set_HIREFIRE_TOKEN):
    config = Configuration().dyno("web")
    with patch.object(config.web, "start") as mock_start:
        for second, request_start in [[0, 500], [0, 1_000], [1, 1_500]]:
            with freeze_time(f"2000-01-01 00:00:0{second}"):
                current_time = int(time.time() * 1_000)
                response = call(config, "/", (current_time - request_start))
                assert None == response
                mock_start.assert_called()
    assert {946684800: [500, 1000], 946684801: [1500]} == config.web._buffer


def test_web_without_request_start_time(set_HIREFIRE_TOKEN):
    config = Configuration().dyno("web")
    with patch.object(config.web, "start") as mock_start:
        response = call(config, "/", None)
        assert None == response
        assert {} == config.web._buffer
        mock_start.assert_not_called()


def test_worker(set_HIREFIRE_TOKEN):
    config = Configuration().dyno("worker", worker_data)
    response = call(config, f"/hirefire/{HIREFIRE_TOKEN}/info")
    headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    }
    assert (200, headers, json.dumps([{"name": "worker", "value": 1.23}])) == response
