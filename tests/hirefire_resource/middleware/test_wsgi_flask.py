import time
from unittest.mock import patch

import pytest
from flask import Flask
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.wsgi import NotConfigured
from hirefire_resource.middleware.wsgi.flask import Middleware
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
    HireFire.configuration = None
    with pytest.raises(NotConfigured):
        client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")


def test_without_HIREFIRE_TOKEN(client):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(HireFire.configuration.web, "start") as mock_start:
        response = client.get(
            "/hirefire/wrong/info",
            headers={"X_REQUEST_START": int(time.time() * 1000 - 5)},
        )
        assert response.status_code == 200
        assert response.data.decode("utf-8") == "DEFAULT"
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_without_web_and_worker(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    headers = {"X_REQUEST_START": int(time.time() * 1000 - 5)}
    response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
    assert response.status_code == 200
    assert response.get_json() == []
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert HireFire.configuration.web is None


@freeze_time("2000-01-01 00:00:00")
def test_web_and_worker(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(HireFire.configuration.web, "start") as mock_start:
        headers = {"X_REQUEST_START": int(time.time() * 1000 - 5)}
        response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
        assert response.status_code == 200
        assert response.get_json() == [{"name": "worker", "value": 1.23}]
        assert response.headers["Content-Type"] == "application/json"
        assert (
            response.headers["cache-control"] == "must-revalidate, private, max-age=0"
        )
        assert HireFire.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


def test_default(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    response = client.get(f"/hirefire/wrong/info")
    assert response.status_code == 200
    assert response.data.decode("utf-8") == "DEFAULT"
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
