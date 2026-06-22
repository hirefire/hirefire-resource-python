import time
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.wsgi.django import HireFireMiddleware
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


class Client:
    def __init__(self, factory):
        self.factory = factory

    def request(self, path, **kwargs):
        request = self.factory.get(path, **kwargs)
        middleware = HireFireMiddleware(self.default_view)
        return middleware(request)

    def default_view(self, request):
        return HttpResponse("DEFAULT")


@pytest.fixture
def client():
    return Client(RequestFactory())


def test_pass_through_without_token(client):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
            config.dyno("worker", lambda: 1.23)
        response = client.request(
            "/", **{"HTTP_X_REQUEST_START": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_without_web(client, set_HIREFIRE_TOKEN):
    response = client.request("/", **{"HTTP_X_REQUEST_START": "1700000000000"})
    assert response.status_code == 200
    assert response.content.decode("utf-8") == "DEFAULT"
    assert HireFire.configuration.buffer.flush()["web"] == {}


@freeze_time("2000-01-01 00:00:00")
def test_samples_web_and_starts_dispatcher(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        response = client.request(
            "/", **{"HTTP_X_REQUEST_START": str(int(time.time() * 1000 - 5))}
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
        response = client.request(
            "/", **{"HTTP_X_QUEUE_START": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
