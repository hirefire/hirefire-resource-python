import json

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware.django import Middleware
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


def test_middleware_match_info_path(factory, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)

    path = f"/hirefire/{HIREFIRE_TOKEN}/info"
    request = factory.get(path)
    middleware = Middleware(default_view)
    response = middleware(request)

    assert {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    } == response.headers
    assert response.status_code == 200
    assert [{"name": "worker", "value": 1.23}] == json.loads(response.content)


def test_middleware_no_match(factory, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)

    path = f"/hirefire/wrong/info"
    request = factory.get(path)
    middleware = Middleware(default_view)
    response = middleware(request)

    assert 200 == response.status_code
    assert "DEFAULT" == response.content.decode("utf-8")
