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
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


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
async def test_samples_without_web_dyno_via_identity(
    client, set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start") as mock_start:
        with patch.object(Dispatcher, "ensure_job_queue_loop") as mock_ensure:
            response = await client.get(
                "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
            )
            assert response.status_code == 200
            assert response.text == "DEFAULT"
            _ts = int(time.time())
            assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
                _ts: {"sum": float(5), "count": 1}
            }
            mock_start.assert_called()
            mock_ensure.assert_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_samples_web_and_starts_dispatcher(
    client, set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start") as mock_start:
        with patch.object(Dispatcher, "ensure_job_queue_loop"):
            response = await client.get(
                "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
            )
            assert response.status_code == 200
            assert response.text == "DEFAULT"
            _ts = int(time.time())
            assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
                _ts: {"sum": float(5), "count": 1}
            }
            mock_start.assert_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_falls_back_to_x_queue_start(client, set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start"):
        with patch.object(Dispatcher, "ensure_job_queue_loop"):
            response = await client.get(
                "/any", headers={"x-queue-start": str(int(time.time() * 1000 - 5))}
            )
            assert response.status_code == 200
            assert response.text == "DEFAULT"
            _ts = int(time.time())
            assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
                _ts: {"sum": float(5), "count": 1}
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_non_http_scopes_pass_through_without_sampling(scope_type):
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["type"])

    async def receive():
        return {}

    async def send(_message):
        pass

    middleware = HireFireMiddleware(inner)
    with patch(
        "hirefire_resource.middleware.asgi.starlette.process_request_queue_time"
    ) as process:
        await middleware({"type": scope_type, "headers": []}, receive, send)

    assert calls == [scope_type]
    process.assert_not_called()
