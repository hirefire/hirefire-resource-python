import copy
import json
import logging
from datetime import datetime
from unittest.mock import patch

import httpretty
import pytest
from freezegun import freeze_time

from hirefire_resource.configuration import Configuration
from hirefire_resource.version import VERSION
from hirefire_resource.web import Web
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


def mock_http_response(status=200, content=""):
    httpretty.register_uri(
        httpretty.POST, "https://logdrain.hirefire.io/", body=content, status=status
    )


@pytest.fixture
def configuration():
    return Configuration()


@pytest.fixture
def web(configuration):
    return Web(configuration)


def test_start_and_stop(web, caplog):
    caplog.set_level(logging.INFO)
    with patch("time.sleep", return_value=None):
        web.start_dispatcher()
        assert web.dispatcher_running() == True
        assert "[HireFire] Starting web metrics dispatcher." in caplog.text
        caplog.clear()
        web.stop_dispatcher()
        assert web.dispatcher_running() == False
        assert "[HireFire] Web metrics dispatcher stopped." in caplog.text


def test_add_to_buffer_and_flush(web):
    with freeze_time("2000-01-01 00:00:00"):
        web.add_to_buffer(5)
        web.add_to_buffer(10)

        timestamp_1 = int(datetime(2000, 1, 1, 0, 0, 0).timestamp())
        assert web._buffer == {timestamp_1: [5, 10]}

    with freeze_time("2000-01-01 00:00:01"):
        web.add_to_buffer(15)
        web.add_to_buffer(20)

        timestamp_2 = int(datetime(2000, 1, 1, 0, 0, 1).timestamp())
        assert web._buffer == {timestamp_1: [5, 10], timestamp_2: [15, 20]}

    data = web._flush_buffer()
    assert data == {timestamp_1: [5, 10], timestamp_2: [15, 20]}
    assert web._buffer == {}


@httpretty.activate
def test_successful_dispatch(web, set_HIREFIRE_TOKEN):
    mock_http_response()
    web.add_to_buffer(5)
    web._dispatch_buffer()
    assert web._buffer == {}


@httpretty.activate
def test_repopulation_and_stdout_on_dispatch_error(web, caplog):
    mock_http_response(status=500)
    web.add_to_buffer(5)
    initial_buffer = copy.deepcopy(web._buffer)
    web._dispatch_buffer()
    assert web._buffer == initial_buffer
    assert "[HireFire] Error while dispatching web metrics:" in caplog.text


@httpretty.activate
def test_submit_buffer_http_information(web, set_HIREFIRE_TOKEN):
    mock_http_response()
    expected_headers = {
        "Content-Type": "application/json",
        "User-Agent": "HireFire Agent (Python)",
        "HireFire-Token": HIREFIRE_TOKEN,
        "HireFire-Resource": f"Python-{VERSION}",
    }
    expected_buffer = {1634367001: [3, 9], 1634367002: [10, 12, 8]}
    expected_buffer_string = json.dumps(expected_buffer)
    web._submit_buffer(expected_buffer)
    last_request = httpretty.last_request()
    assert "POST" == last_request.method
    assert expected_buffer_string == last_request.body.decode("utf-8")
    for header, value in expected_headers.items():
        assert value == last_request.headers.get(header)
