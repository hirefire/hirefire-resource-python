import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from starlette.responses import Response
from starlette.testclient import TestClient

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.asgi.django import HireFireMiddleware
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


async def app(scope, receive, send):
    if scope["type"] == "http":
        response = Response(content="DEFAULT", media_type="text/html")
        await response(scope, receive, send)
    elif scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


asgi_app = HireFireMiddleware(app)


@pytest.fixture
def client():
    with TestClient(asgi_app) as client:
        yield client


def test_pass_through_without_token(client):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
            config.dyno("worker", lambda: 1.23)
        response = client.get(
            "/any", headers={"x-request-start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.content == b"DEFAULT"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_pass_through_without_web(client, set_HIREFIRE_TOKEN):
    response = client.get("/any", headers={"x-request-start": "1700000000000"})
    assert response.status_code == 200
    assert response.content == b"DEFAULT"
    assert HireFire.configuration.buffer.flush()["web"] == {}


@freeze_time("2000-01-01 00:00:00")
def test_samples_web_and_starts_dispatcher(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        response = client.get(
            "/any", headers={"x-request-start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.content == b"DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
def test_falls_back_to_x_queue_start(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("web")
        response = client.get(
            "/any", headers={"x-queue-start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.content == b"DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
