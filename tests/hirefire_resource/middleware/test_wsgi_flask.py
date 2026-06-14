import time
from unittest.mock import patch

import pytest
from flask import Flask
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa: F401

app = Flask(__name__)
app.wsgi_app = HireFireMiddleware(app)
app.config["TESTING"] = True


@app.route("/<path:path>")
def catch_all(path):
    return "DEFAULT", 200


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


def test_pass_through_without_token(client):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
            config.dyno("worker", lambda: 1.23)
        response = client.get(
            "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.data.decode("utf-8") == "DEFAULT"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_without_web(client, set_HIREFIRE_TOKEN):
    response = client.get("/any", headers={"X-Request-Start": "1700000000000"})
    assert response.status_code == 200
    assert response.data.decode("utf-8") == "DEFAULT"
    assert HireFire.configuration.buffer.flush()["web"] == {}


@freeze_time("2000-01-01 00:00:00")
def test_samples_web_and_starts_dispatcher(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        response = client.get(
            "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.data.decode("utf-8") == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
def test_info_path_passes_through(client, set_HIREFIRE_TOKEN):
    # The push model serves no inline endpoint; the legacy /info path is just an
    # ordinary request now.
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("worker", lambda: 1.23)
        response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")
        assert response.status_code == 200
        assert response.data.decode("utf-8") == "DEFAULT"
