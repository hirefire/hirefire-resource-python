import time
from unittest.mock import patch

import pytest
from flask import Flask
from freezegun import freeze_time

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware import NotConfigured
from hirefire_resource.middleware.flask import Middleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

app = Flask(__name__)
app.wsgi_app = Middleware(app)
app.config["TESTING"] = True


@app.route("/<path:path>")
def catch_all(path):
    return "DEFAULT", 200


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


def test_without_configuration(client, set_HIREFIRE_TOKEN):
    Resource.configuration = None
    with pytest.raises(NotConfigured):
        client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")


@freeze_time("2000-01-01 00:00:00")
def test_without_web_and_worker(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration()
    headers = {"X_REQUEST_START": int(time.time() * 1000 - 5)}
    response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
    assert 200 == response.status_code
    assert [] == response.get_json()
    assert "application/json" == response.headers["Content-Type"]
    assert "must-revalidate, private, max-age=0" == response.headers["cache-control"]
    assert None == Resource.configuration.web


@freeze_time("2000-01-01 00:00:00")
def test_web_and_worker(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(Resource.configuration.web, "start") as mock_start:
        headers = {"X_REQUEST_START": int(time.time() * 1000 - 5)}
        response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
        assert 200 == response.status_code
        assert [{"name": "worker", "value": 1.23}] == response.get_json()
        assert "application/json" == response.headers["Content-Type"]
        assert (
            "must-revalidate, private, max-age=0" == response.headers["cache-control"]
        )
        assert {946684800: [5]} == Resource.configuration.web._buffer
        mock_start.assert_called()


def test_default(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    response = client.get(f"/hirefire/wrong/info")
    assert 200 == response.status_code
    assert "DEFAULT" == response.data.decode("utf-8")
    assert "text/html; charset=utf-8" == response.headers["Content-Type"]
