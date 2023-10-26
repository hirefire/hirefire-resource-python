import json
import time

import httpretty

from autoscale_agent.worker_dispatcher import WorkerDispatcher
from tests.helpers import TOKEN


def test_token():
    dispatcher = WorkerDispatcher(TOKEN, lambda: 1.23)
    assert dispatcher.token == TOKEN


def test_id():
    dispatcher = WorkerDispatcher(TOKEN, lambda: 1.23)
    assert dispatcher.id == "u4quBFg"


@httpretty.activate(verbose=True, allow_net_connect=False)
def test_dispatch():
    httpretty.register_uri(
        httpretty.POST,
        "https://metrics.autoscale.app/",
        status=200,
        body="",
        headers={},
    )
    dispatcher = WorkerDispatcher(TOKEN, lambda: 1.23)
    dispatcher.dispatch()
    last_request = httpretty.last_request()
    expected_body = {str(int(time.time())): 1.23}
    actual_body = json.loads(last_request.body.decode("utf-8"))
    assert expected_body == actual_body
    assert last_request.headers["Autoscale-Metric-Token"] == TOKEN
    assert last_request.headers["Content-Type"] == "application/json"
    assert last_request.headers["User-Agent"] == "Autoscale Agent (Python)"


@httpretty.activate(verbose=True, allow_net_connect=False)
def test_dispatch_500(capsys):
    httpretty.register_uri(
        httpretty.POST,
        "https://metrics.autoscale.app/",
        status=500,
        body="",
        headers={},
    )
    dispatcher = WorkerDispatcher(TOKEN, lambda: 1.23)
    out, _ = capsys.readouterr()
    dispatcher.dispatch()
    last_request = httpretty.last_request()
    expected_body = {str(int(time.time())): 1.23}
    actual_body = json.loads(last_request.body.decode())
    assert expected_body == actual_body
    assert last_request.headers["Autoscale-Metric-Token"] == TOKEN
    assert last_request.headers["Content-Type"] == "application/json"
    assert last_request.headers["User-Agent"] == "Autoscale Agent (Python)"
    out, _ = capsys.readouterr()
    assert "Autoscale[u4quBFg][ERROR]: Failed to dispatch (500) " in out


def test_dispatch_nil_value(capsys):
    dispatcher = WorkerDispatcher(TOKEN, lambda: None)
    out, _ = capsys.readouterr()
    dispatcher.dispatch()
    out, _ = capsys.readouterr()
    assert "Autoscale[u4quBFg][ERROR]: No value to dispatch (None)" in out
