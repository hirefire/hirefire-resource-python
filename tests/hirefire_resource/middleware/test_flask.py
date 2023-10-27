import time
from unittest.mock import patch

import pytest
from flask import Flask
from freezegun import freeze_time

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware.flask import Middleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

app = Flask(__name__)
app.wsgi_app = Middleware(app)

app.config["TESTING"] = True


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


@freeze_time("2000-01-01 00:00:00")
def test_flask_middleware_info_path(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)

    with patch.object(Resource.configuration.web, "start") as mock_start:
        headers = {"HTTP_X_REQUEST_START": int(time.time() * 1000 - 5)}
        response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)

        assert 200 == response.status_code
        assert [{"name": "worker", "value": 1.23}] == response.get_json()
        assert "application/json" == response.headers["Content-Type"]
        assert (
            "must-revalidate, private, max-age=0" == response.headers["cache-control"]
        )


# def test_flask_middleware_wrong_path(client):
#     response = client.get("/some/wrong/path")
#     assert response.status_code == 404
