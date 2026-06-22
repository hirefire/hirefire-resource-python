import time
from unittest.mock import patch

import httpx
import pytest
from django.core.asgi import get_asgi_application
from django.http import HttpResponse
from django.test import override_settings
from django.urls import re_path
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.asgi.django import HireFireMiddleware
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


# A minimal real Django ASGI app: a single catch-all view, with the HireFire
# ASGI middleware wrapping Django's real ASGI application. Requests run through
# httpx's ASGI transport into Django's ASGI handler and URL resolver.
async def catch_all(request):
    return HttpResponse("DEFAULT")


urlpatterns = [re_path(r"^.*$", catch_all)]

application = HireFireMiddleware(get_asgi_application())


@pytest.fixture(autouse=True)
def app():
    with override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"]):
        yield


@pytest.fixture
def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    )


@pytest.mark.asyncio
async def test_pass_through_without_token(client):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
            config.dyno("worker", lambda: 1.23)
        response = await client.get(
            "/any", headers={"x-request-start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.text == "DEFAULT"
        mock_start.assert_not_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_pass_through_without_web(client, set_HIREFIRE_TOKEN):
    response = await client.get("/any", headers={"x-request-start": "1700000000000"})
    assert response.status_code == 200
    assert response.text == "DEFAULT"
    assert HireFire.configuration.buffer.flush()["web"] == {}


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_samples_web_and_starts_dispatcher(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        response = await client.get(
            "/any", headers={"x-request-start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.text == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
        mock_start.assert_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_falls_back_to_x_queue_start(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("web")
        response = await client.get(
            "/any", headers={"x-queue-start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.text == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
