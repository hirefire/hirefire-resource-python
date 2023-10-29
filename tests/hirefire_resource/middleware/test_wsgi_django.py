import json
import time
from unittest.mock import patch

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory
from freezegun import freeze_time

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware.wsgi import NotConfigured
from hirefire_resource.middleware.wsgi.django import Middleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

settings.configure(
    SECRET_KEY="dummy-secret-key",
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    },
)


@pytest.fixture
def factory():
    return RequestFactory()


def default_view(request):
    return HttpResponse("DEFAULT")


def test_without_configuration(factory, set_HIREFIRE_TOKEN):
    Resource.configuration = None
    with pytest.raises(NotConfigured):
        request = factory.get("/")
        middleware = Middleware(default_view)
        middleware(request)


@freeze_time("2000-01-01 00:00:00")
def test_without_web_and_worker(factory, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration()
    path = f"/hirefire/{HIREFIRE_TOKEN}/info"
    request = factory.get(path, **{"HTTP_X_REQUEST_START": int(time.time() * 1000 - 5)})
    middleware = Middleware(default_view)
    response = middleware(request)
    assert {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    } == response.headers
    assert response.status_code == 200
    assert [] == json.loads(response.content)
    assert None == Resource.configuration.web


@freeze_time("2000-01-01 00:00:00")
def test_web_and_worker(factory, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(Resource.configuration.web, "start") as mock_start:
        path = f"/hirefire/{HIREFIRE_TOKEN}/info"
        request = factory.get(
            path, **{"HTTP_X_REQUEST_START": int(time.time() * 1000 - 5)}
        )
        middleware = Middleware(default_view)
        response = middleware(request)
        assert {
            "content-type": "application/json",
            "cache-control": "must-revalidate, private, max-age=0",
        } == response.headers
        assert response.status_code == 200
        assert [{"name": "worker", "value": 1.23}] == json.loads(response.content)
        assert {946684800: [5]} == Resource.configuration.web._buffer
        mock_start.assert_called()


def test_default(factory, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    path = f"/hirefire/wrong/info"
    request = factory.get(path)
    middleware = Middleware(default_view)
    response = middleware(request)
    assert 200 == response.status_code
    assert "DEFAULT" == response.content.decode("utf-8")
