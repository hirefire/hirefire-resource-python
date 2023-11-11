import json
import time
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.wsgi import NotConfigured
from hirefire_resource.middleware.wsgi.django import Middleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


@pytest.fixture
def factory():
    return RequestFactory()


def default_view(request):
    return HttpResponse("DEFAULT")


def test_without_configuration(factory, set_HIREFIRE_TOKEN):
    HireFire.configuration = None
    with pytest.raises(NotConfigured):
        request = factory.get("/")
        middleware = Middleware(default_view)
        middleware(request)


def test_without_HIREFIRE_TOKEN(factory):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(HireFire.configuration.web, "start") as mock_start:
        request = factory.get(
            "/hirefire/wrong/info",
            **{"HTTP_X_REQUEST_START": int(time.time() * 1000 - 5)},
        )
        middleware = Middleware(default_view)
        response = middleware(request)
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        assert response["content-type"] == "text/html; charset=utf-8"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_without_web_and_worker(factory, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    path = f"/hirefire/{HIREFIRE_TOKEN}/info"
    request = factory.get(path, **{"HTTP_X_REQUEST_START": int(time.time() * 1000 - 5)})
    middleware = Middleware(default_view)
    response = middleware(request)
    expected_headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    }
    assert response.headers == expected_headers
    assert response.status_code == 200
    assert json.loads(response.content) == []
    assert HireFire.configuration.web is None


@freeze_time("2000-01-01 00:00:00")
def test_web_and_worker(factory, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(HireFire.configuration.web, "start") as mock_start:
        path = f"/hirefire/{HIREFIRE_TOKEN}/info"
        request = factory.get(
            path, **{"HTTP_X_REQUEST_START": int(time.time() * 1000 - 5)}
        )
        middleware = Middleware(default_view)
        response = middleware(request)
        expected_headers = {
            "content-type": "application/json",
            "cache-control": "must-revalidate, private, max-age=0",
        }
        assert response.headers == expected_headers
        assert response.status_code == 200
        assert json.loads(response.content) == [{"name": "worker", "value": 1.23}]
        assert HireFire.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


def test_default(factory, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    path = f"/hirefire/wrong/info"
    request = factory.get(path)
    middleware = Middleware(default_view)
    response = middleware(request)
    assert response.status_code == 200
    assert response.content.decode("utf-8") == "DEFAULT"
