import time
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import re_path
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


# A minimal real Django app: a single catch-all view behind the HireFire
# middleware. Requests run through Django's real WSGI handler, middleware chain,
# and URL resolver via the test Client.
def catch_all(request):
    return HttpResponse("DEFAULT")


urlpatterns = [re_path(r"^.*$", catch_all)]


@pytest.fixture(autouse=True)
def app():
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["testserver"],
        MIDDLEWARE=["hirefire_resource.middleware.wsgi.django.HireFireMiddleware"],
    ):
        yield


@pytest.fixture
def client():
    return Client()


def test_pass_through_without_token(client):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
            config.dyno("worker", lambda: 1.23)
        response = client.get(
            "/any", HTTP_X_REQUEST_START=str(int(time.time() * 1000 - 5))
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_without_web(client, set_HIREFIRE_TOKEN):
    response = client.get("/any", HTTP_X_REQUEST_START="1700000000000")
    assert response.status_code == 200
    assert response.content.decode("utf-8") == "DEFAULT"
    assert HireFire.configuration.buffer.flush()["web"] == {}


@freeze_time("2000-01-01 00:00:00")
def test_samples_web_and_starts_dispatcher(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        response = client.get(
            "/any", HTTP_X_REQUEST_START=str(int(time.time() * 1000 - 5))
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
def test_falls_back_to_x_queue_start(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("web")
        response = client.get(
            "/any", HTTP_X_QUEUE_START=str(int(time.time() * 1000 - 5))
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
