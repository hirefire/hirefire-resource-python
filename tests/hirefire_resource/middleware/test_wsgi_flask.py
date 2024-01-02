import time
from unittest.mock import patch

import pytest
from flask import Flask
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

app = Flask(__name__)
app.wsgi_app = HireFireMiddleware(app)
app.config["TESTING"] = True


@app.route("/<path:path>")
def catch_all(path):
    return "DEFAULT", 200


@pytest.fixture(autouse=True)
def setup():
    HireFire.configuration = Configuration()
    yield


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


def measure_queue_metric():
    return 1.23


def test_pass_through_without_HIREFIRE_TOKEN(client):
    with HireFire.configure() as config:
        config.dyno("web")
        config.dyno("worker", measure_queue_metric)
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = client.get(
            "/any", headers={"X_REQUEST_START": int(time.time() * 1000 - 5)}
        )
        assert response.status_code == 200
        assert response.data.decode("utf-8") == "DEFAULT"
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_without_configuration(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    response = client.get("/any", headers={"X_REQUEST_START": "1"})
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.data.decode("utf-8") == "DEFAULT"


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_and_process_web_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("web")
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = client.get(
            "/any", headers={"X_REQUEST_START": int(time.time() * 1000 - 5)}
        )
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        assert response.data.decode("utf-8") == "DEFAULT"
        assert HireFire.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
def test_intercept_and_process_worker_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.get(
        f"/hirefire/{HIREFIRE_TOKEN}/info", headers={"X_REQUEST_START": "1"}
    )
    assert response.status_code == 200
    assert response.get_json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["Cache-Control"] == "must-revalidate, private, max-age=0"
    assert response.headers["Hirefire-Resource"] == f"Python-{VERSION}"


@freeze_time("2000-01-01 00:00:00")
def test_intercept_and_process_worker_configuration_with_token(
    client, set_HIREFIRE_TOKEN
):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.get(
        f"/hirefire", headers={"X_REQUEST_START": "1", "HIREFIRE_TOKEN": HIREFIRE_TOKEN}
    )
    assert response.status_code == 200
    assert response.get_json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["Cache-Control"] == "must-revalidate, private, max-age=0"
    assert response.headers["Hirefire-Resource"] == f"Python-{VERSION}"
