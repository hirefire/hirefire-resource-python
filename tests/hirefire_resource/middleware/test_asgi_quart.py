import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from quart import Quart

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.asgi.quart import HireFireMiddleware
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401

app = Quart(__name__)
app.asgi_app = HireFireMiddleware(app)
app.config["TESTING"] = True


async def catch_all(path):
    return "DEFAULT", 200


app.add_url_rule("/<path:path>", "catch_all", catch_all)


@pytest.fixture
def client():
    return app.test_client()


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
        assert (await response.get_data(as_text=True)) == "DEFAULT"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_samples_without_web_dyno_via_identity(
    set_HIREFIRE_TOKEN, client, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start") as mock_start:
        with patch.object(Dispatcher, "ensure_job_queue_loop") as mock_ensure:
            response = await client.get(
                "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
            )
            assert response.status_code == 200
            assert (await response.get_data(as_text=True)) == "DEFAULT"
            _ts = int(time.time())
            assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
                _ts: {"sum": float(5), "count": 1}
            }
            mock_start.assert_called()
            mock_ensure.assert_called()


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_samples_web_and_starts_dispatcher(
    set_HIREFIRE_TOKEN, client, monkeypatch
):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start") as mock_start:
        with patch.object(Dispatcher, "ensure_job_queue_loop"):
            response = await client.get(
                "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
            )
            assert response.status_code == 200
            assert (await response.get_data(as_text=True)) == "DEFAULT"
            _ts = int(time.time())
            assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
                _ts: {"sum": float(5), "count": 1}
            }
            mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_falls_back_to_x_queue_start(set_HIREFIRE_TOKEN, client, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start"):
        with patch.object(Dispatcher, "ensure_job_queue_loop"):
            response = await client.get(
                "/any", headers={"x-queue-start": str(int(time.time() * 1000 - 5))}
            )
            assert response.status_code == 200
            assert (await response.get_data(as_text=True)) == "DEFAULT"
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

    middleware = HireFireMiddleware(SimpleNamespace(asgi_app=inner))
    with patch(
        "hirefire_resource.middleware.asgi.quart.process_request_queue_time"
    ) as process:
        await middleware({"type": scope_type, "headers": []}, receive, send)

    assert calls == [scope_type]
    process.assert_not_called()
