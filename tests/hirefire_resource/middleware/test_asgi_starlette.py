import time
from unittest.mock import patch

import httpx
import pytest
from freezegun import freeze_time
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware.asgi import NotConfigured
from hirefire_resource.middleware.asgi.starlette import Middleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


async def catch_all(request):
    return PlainTextResponse("DEFAULT")


routes = [Route("/{path:path}", catch_all)]
app = Starlette(routes=routes)
app = Middleware(app)


@pytest.fixture
def client():
    return httpx.AsyncClient(app=app, base_url="http://test")


@pytest.mark.asyncio
async def test_without_configuration(client, set_HIREFIRE_TOKEN):
    Resource.configuration = None
    with pytest.raises(NotConfigured):
        await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_without_web_and_worker(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration()
    headers = {"X_REQUEST_START": str(int(time.time() * 1000 - 5))}
    response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
    assert 200 == response.status_code
    assert [] == response.json()
    assert "application/json" == response.headers["Content-Type"]
    assert "must-revalidate, private, max-age=0" == response.headers["cache-control"]
    assert None == Resource.configuration.web


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_web_and_worker(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(Resource.configuration.web, "start") as mock_start:
        headers = {"X_REQUEST_START": str(int(time.time() * 1000 - 5))}
        response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
        assert 200 == response.status_code
        assert [{"name": "worker", "value": 1.23}] == response.json()
        assert "application/json" == response.headers["Content-Type"]
        assert (
            "must-revalidate, private, max-age=0" == response.headers["cache-control"]
        )
        assert {946684800: [5]} == Resource.configuration.web._buffer
        mock_start.assert_called()


@pytest.mark.asyncio
async def test_default(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    response = await client.get(f"/hirefire/wrong/info")
    assert 200 == response.status_code
    assert "DEFAULT" == response.text
    assert "text/plain; charset=utf-8" == response.headers["Content-Type"]
