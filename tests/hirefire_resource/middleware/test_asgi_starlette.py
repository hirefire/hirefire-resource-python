import time
from unittest.mock import patch

import httpx
import pytest
from freezegun import freeze_time
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
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
    HireFire.configuration = None
    with pytest.raises(NotConfigured):
        await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")


@pytest.mark.asyncio
async def test_without_HIREFIRE_TOKEN(client):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(HireFire.configuration.web, "start") as mock_start:
        response = await client.get(
            "/hirefire/wrong/info",
            headers={"x-request-start": str(int(time.time() * 1000 - 5))},
        )
        assert response.status_code == 200
        assert response.text == "DEFAULT"
        assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
        mock_start.assert_not_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_without_web_and_worker(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    headers = {"X-Request-Start": str(int(time.time() * 1000 - 5))}
    response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert HireFire.configuration.web is None


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_web_and_worker(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(HireFire.configuration.web, "start") as mock_start:
        headers = {"X-Request-Start": str(int(time.time() * 1000 - 5))}
        response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
        assert response.status_code == 200
        assert response.json() == [{"name": "worker", "value": 1.23}]
        assert response.headers["Content-Type"] == "application/json"
        assert (
            response.headers["cache-control"] == "must-revalidate, private, max-age=0"
        )
        assert HireFire.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


@pytest.mark.asyncio
async def test_default(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    response = await client.get(f"/hirefire/wrong/info")
    assert response.status_code == 200
    assert response.text == "DEFAULT"
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
