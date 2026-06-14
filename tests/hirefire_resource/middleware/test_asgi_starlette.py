import time
from unittest.mock import patch

import httpx
import pytest
from freezegun import freeze_time
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.asgi.starlette import HireFireMiddleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa: F401


async def catch_all(request):
    return PlainTextResponse("DEFAULT")


app = Starlette(routes=[Route("/{path:path}", catch_all)])
app = HireFireMiddleware(app)


@pytest.fixture
def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
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
    response = await client.get("/any", headers={"X-Request-Start": "1700000000000"})
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
            "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.text == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
        mock_start.assert_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_info_path_passes_through(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("worker", lambda: 1.23)
        response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")
        assert response.status_code == 200
        assert response.text == "DEFAULT"
