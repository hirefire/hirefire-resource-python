import json
from unittest.mock import patch

import httpretty
from freezegun import freeze_time

from autoscale_agent.web_dispatcher import WebDispatcher
from tests.helpers import TOKEN


def test_token():
    dispatcher = WebDispatcher(TOKEN)
    assert "u4quBFgM72qun74EwashWv6Ll5TzhBVktVmicoWoXla" == dispatcher.token


def test_id():
    dispatcher = WebDispatcher(TOKEN)
    assert "u4quBFg" == dispatcher.id


def test_add():
    with freeze_time("2000-01-01 00:00:00"):
        dispatcher = WebDispatcher(TOKEN)
        dispatcher.add(1)
        assert dispatcher._buffer == {946684800: 1}


def test_add_ttl_exceeded():
    with freeze_time("2000-01-01 00:00:00"):
        dispatcher = WebDispatcher(TOKEN)
        dispatcher.add(1, 946684800 - WebDispatcher.TTL)
        assert dispatcher._buffer == {}


@httpretty.activate(verbose=True, allow_net_connect=False)
def test_dispatch():
    httpretty.register_uri(
        httpretty.POST,
        "https://metrics.autoscale.app/",
        status=200,
        body="",
        headers={},
    )
    dispatcher = WebDispatcher(TOKEN)
    metrics = [[1], [0, 2, 1], [2, 1, 3], [1, 4, 3], [5, 4, 1], [6, 2, 6], [0, 3, 7]]
    for i in range(len(metrics)):
        with freeze_time(f"2000-01-01 00:00:{i+1}"):
            for metric in metrics[i]:
                dispatcher.add(metric)
    with freeze_time("2000-01-01 00:00:07"):
        dispatcher.dispatch()
    request = httpretty.last_request()
    assert request.headers.get("Content-Type") == "application/json"
    assert request.headers.get("User-Agent") == "Autoscale Agent (Python)"
    assert request.headers.get("Autoscale-Metric-Token") == TOKEN
    actual_body = json.loads(request.body.decode("utf-8"))
    expected_body = {
        "946684801": 1,
        "946684802": 2,
        "946684803": 3,
        "946684804": 4,
        "946684805": 5,
        "946684806": 6,
        "946684807": 7,
    }
    assert actual_body == expected_body
    assert dispatcher._buffer == {}


@patch("autoscale_agent.util.dispatch")
def test_dispatch_empty(mock_dispatch):
    dispatcher = WebDispatcher(TOKEN)
    dispatcher.dispatch()
    mock_dispatch.assert_not_called()


@httpretty.activate(verbose=True, allow_net_connect=False)
def test_dispatch_500(capsys):
    dispatcher = WebDispatcher(TOKEN)
    metrics = [[1], [0, 2, 1], [2, 1, 3], [1, 4, 3], [5, 4, 1], [6, 2, 6], [0, 3, 7]]
    for i in range(len(metrics)):
        with freeze_time(f"2000-01-01 00:00:{i+1}"):
            for metric in metrics[i]:
                dispatcher.add(metric)
    buffer = dispatcher._buffer.copy()
    httpretty.register_uri(
        httpretty.POST,
        "https://metrics.autoscale.app/",
        status=500,
        body="",
        headers={},
    )
    with freeze_time("2000-01-01 00:00:07"):
        dispatcher.dispatch()
    assert buffer == dispatcher._buffer
    request = httpretty.last_request()
    assert request.headers.get("Content-Type") == "application/json"
    assert request.headers.get("User-Agent") == "Autoscale Agent (Python)"
    assert request.headers.get("Autoscale-Metric-Token") == TOKEN
    actual_body = json.loads(request.body.decode("utf-8"))
    expected_body = {
        "946684801": 1,
        "946684802": 2,
        "946684803": 3,
        "946684804": 4,
        "946684805": 5,
        "946684806": 6,
        "946684807": 7,
    }
    assert actual_body == expected_body
    out, _ = capsys.readouterr()
    assert "Autoscale[u4quBFg][ERROR]: Failed to dispatch data (500)" in out
    assert buffer == dispatcher._buffer


@patch("threading.Thread")
def test_run(mock_thread):
    dispatcher = WebDispatcher(TOKEN)
    dispatcher.run()
    mock_thread.assert_called_once_with(target=dispatcher._run_loop, daemon=True)
