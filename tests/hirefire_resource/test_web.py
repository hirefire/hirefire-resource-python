import copy
import json
import logging
import socket
from datetime import datetime
from unittest.mock import patch

import httpretty
import pytest
from freezegun import freeze_time

from hirefire_resource.configuration import Configuration
from hirefire_resource.version import VERSION
from hirefire_resource.web import DispatchError, Web
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
        assert web.start_dispatcher() == True
        assert web.dispatcher_running() == True
        assert web.start_dispatcher() == False
        assert "[HireFire] Starting web metrics dispatcher." in caplog.text
        caplog.clear()
        assert web.stop_dispatcher() == True
        assert web.dispatcher_running() == False
        assert web.stop_dispatcher() == False
        assert web._flush_buffer() == {}
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
def test_http_exception_handling(web, caplog, set_HIREFIRE_TOKEN):
    httpretty.register_uri(
        httpretty.POST, "https://logdrain.hirefire.io/", status=500, body="Server Error"
    )
    web.add_to_buffer(5)
    web._dispatch_buffer()
    assert "HTTP error occurred:" in caplog.text


@httpretty.activate
def test_socket_timeout_handling(web, caplog, set_HIREFIRE_TOKEN):
    with patch("http.client.HTTPSConnection.request", side_effect=socket.timeout):
        web.add_to_buffer(5)
        web._dispatch_buffer()
        assert "The request to the server timed out." in caplog.text


@httpretty.activate
def test_generic_exception_handling(web, caplog, set_HIREFIRE_TOKEN):
    with patch(
        "http.client.HTTPSConnection.request", side_effect=Exception("Generic Error")
    ):
        web.add_to_buffer(5)
        web._dispatch_buffer()
    assert "Error occurred during request: Generic Error" in caplog.text


@httpretty.activate
def test_submit_buffer_http_information(web, set_HIREFIRE_TOKEN):
    mock_http_response()
    expected_headers = {
        "Content-Type": "application/json",
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


@httpretty.activate
def test_buffer_ttl_discards_old_entries(web, set_HIREFIRE_TOKEN):
    with freeze_time("2000-01-01 00:00:00"):
        web.add_to_buffer(5)
        timestamp_1 = int(datetime(2000, 1, 1, 0, 0, 0).timestamp())
        assert web._buffer == {timestamp_1: [5]}
    with freeze_time("2000-01-01 00:00:30"):
        web.add_to_buffer(10)
        timestamp_2 = int(datetime(2000, 1, 1, 0, 0, 30).timestamp())
        assert web._buffer == {timestamp_1: [5], timestamp_2: [10]}
    with freeze_time("2000-01-01 00:01:00"):
        mock_http_response(status=500)
        web._dispatch_buffer()
        assert web._buffer == {timestamp_1: [5], timestamp_2: [10]}
    with freeze_time("2000-01-01 00:01:01"):
        mock_http_response(status=500)
        web._dispatch_buffer()
        assert web._buffer == {timestamp_2: [10]}


@httpretty.activate
def test_adjust_parameters_based_on_response_headers(web, set_HIREFIRE_TOKEN):
    httpretty.register_uri(
        httpretty.POST,
        "https://logdrain.hirefire.io/",
        adding_headers={
            "HireFire-Resource-Dispatch-Interval": "10",
            "HireFire-Resource-Dispatch-Timeout": "10",
            "HireFire-Resource-Buffer-TTL": "120",
        },
        status=200,
    )
    web.add_to_buffer(5)
    web._dispatch_buffer()
    assert web._dispatch_interval == 10
    assert web._dispatch_timeout == 10
    assert web._buffer_ttl == 120


def test_submit_buffer_without_hirefire_token(web, caplog):
    with pytest.raises(DispatchError) as exc_info:
        web._submit_buffer({})
    assert str(exc_info.value) == (
        "The HIREFIRE_TOKEN environment variable is not set. Unable to submit "
        "Request Queue Time metric data. The HIREFIRE_TOKEN can be found in "
        "the HireFire Web UI in the web dyno manager settings."
    )


@httpretty.activate
def test_submit_buffer_with_custom_dispatch_url(web, set_HIREFIRE_TOKEN, monkeypatch):
    custom_dispatch_host = "custom.hirefire.io"
    custom_dispatch_url = f"https://{custom_dispatch_host}"
    monkeypatch.setenv("HIREFIRE_DISPATCH_URL", custom_dispatch_url)
    httpretty.register_uri(httpretty.POST, custom_dispatch_url, status=200)
    web.add_to_buffer(5)
    web._submit_buffer({1634367001: [5]})
    last_request = httpretty.last_request()
    assert last_request.headers.get("host") == custom_dispatch_host
