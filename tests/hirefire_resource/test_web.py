import copy
import json
from datetime import datetime
from unittest.mock import patch

import httpretty
from freezegun import freeze_time

from hirefire_resource import __version__
from hirefire_resource.web import Web
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


def mock_http_response(status=200, content=""):
    httpretty.register_uri(
        httpretty.POST, "https://logdrain.hirefire.io/", body=content, status=status
    )


def test_start_and_stop():
    with patch("time.sleep", return_value=None):
        web = Web()
        web.start()
        assert web.running() == True
        web.stop()
        assert web.running() == False


def test_add_to_buffer_and_flush():
    web = Web()

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

    data = web.flush()
    assert data == {timestamp_1: [5, 10], timestamp_2: [15, 20]}
    assert web._buffer == {}


@httpretty.activate
def test_successful_dispatch(set_HIREFIRE_TOKEN):
    mock_http_response()
    web = Web()
    web.add_to_buffer(5)
    web.dispatch()
    assert web._buffer == {}


@httpretty.activate
def test_repopulation_and_stdout_on_dispatch_error(capsys):
    mock_http_response(status=500)
    web = Web()
    web.add_to_buffer(5)
    initial_buffer = copy.deepcopy(web._buffer)

    web.dispatch()

    assert web._buffer == initial_buffer
    assert "[HireFire] Error while dispatching web metrics:" in capsys.readouterr().out


@httpretty.activate
def test_submit_buffer_http_information(set_HIREFIRE_TOKEN):
    mock_http_response()

    expected_headers = {
        "Content-Type": "application/json",
        "User-Agent": "HireFire Agent (Python)",
        "HireFire-Token": HIREFIRE_TOKEN,
        "HireFire-Resource": f"Python-{__version__}",
    }
    expected_buffer = {1634367001: [3, 9], 1634367002: [10, 12, 8]}
    expected_buffer_string = json.dumps(expected_buffer)

    web = Web()

    web._submit_buffer(expected_buffer)

    last_request = httpretty.last_request()

    assert "POST" == last_request.method
    assert expected_buffer_string == last_request.body.decode("utf-8")
    for header, value in expected_headers.items():
        assert value == last_request.headers.get(header)
