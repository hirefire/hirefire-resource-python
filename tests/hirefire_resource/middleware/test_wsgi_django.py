import json
import time
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.wsgi.django import HireFireMiddleware
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


class Client:
    def __init__(self, factory):
        self.factory = factory

    def request(self, path, **kwargs):
        request = self.factory.get(path, **kwargs)
        middleware = HireFireMiddleware(self.default_view)
        return middleware(request)

    def default_view(self, request):
        return HttpResponse("DEFAULT")


@pytest.fixture(autouse=True)
def setup():
    HireFire.configuration = Configuration()
    yield


@pytest.fixture
def client():
    return Client(RequestFactory())


def measure_queue_metric():
    return 1.23


def test_pass_through_without_HIREFIRE_TOKEN(client):
    with HireFire.configure() as config:
        config.dyno("web")
        config.dyno("worker", measure_queue_metric)
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = client.request("/", **{"HTTP_X_REQUEST_START": "1"})
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        assert response["content-type"] == "text/html; charset=utf-8"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_without_configuration(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    response = client.request("/", **{"HTTP_X_REQUEST_START": "1"})
    assert response.status_code == 200
    assert response["content-type"] == "text/html; charset=utf-8"
    assert response.content.decode("utf-8") == "DEFAULT"


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_and_process_web_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("web")
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = client.request(
            "/", **{"HTTP_X_REQUEST_START": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response["content-type"] == "text/html; charset=utf-8"
        assert response.content.decode("utf-8") == "DEFAULT"
        assert HireFire.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
def test_intercept_and_process_worker_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.request(
        f"/hirefire/{HIREFIRE_TOKEN}/info", **{"HTTP_X_REQUEST_START": "1"}
    )
    expected_headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
        "hirefire-resource": f"Python-{VERSION}",
    }
    assert response.headers == expected_headers
    assert response.status_code == 200
    assert json.loads(response.content) == [{"name": "worker", "value": 1.23}]


@freeze_time("2000-01-01 00:00:00")
def test_intercept_and_process_worker_configuration_with_token(
    client, set_HIREFIRE_TOKEN
):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.request(
        f"/hirefire",
        **{"HTTP_X_REQUEST_START": "1", "HTTP_HIREFIRE_TOKEN": HIREFIRE_TOKEN},
    )
    expected_headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
        "hirefire-resource": f"Python-{VERSION}",
    }
    assert response.headers == expected_headers
    assert response.status_code == 200
    assert json.loads(response.content) == [{"name": "worker", "value": 1.23}]
