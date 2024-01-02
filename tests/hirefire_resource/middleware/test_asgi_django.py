import time
from datetime import datetime
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from starlette.responses import Response
from starlette.testclient import TestClient

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.asgi.django import HireFireMiddleware
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


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


@pytest.fixture(autouse=True)
def setup():
    HireFire.configuration = Configuration()
    yield


@pytest.fixture
def client():
    with TestClient(asgi_app) as client:
        yield client


async def measure_queue_metric():
    return 1.23


@pytest.mark.asyncio
async def test_pass_through_without_HIREFIRE_TOKEN(client):
    with HireFire.configure() as config:
        config.dyno("web")
        config.dyno("worker", measure_queue_metric)
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = client.get("/", headers={"x-request-start": "1"})
        assert response.status_code == 200
        assert response.content == b"DEFAULT"
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_pass_through_without_configuration(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    response = client.get("/any", headers={"x-request-start": "1"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.content == b"DEFAULT"


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_pass_through_and_process_web_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("web")
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = client.get(
            "/", headers={"x-request-start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.content == b"DEFAULT"
        frozen_time = datetime.now().timestamp()
        assert HireFire.configuration.web._buffer == {int(frozen_time): [5]}
        mock_start.assert_called_once()


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_intercept_and_process_worker_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.get(
        f"/hirefire/{HIREFIRE_TOKEN}/info", headers={"x-request-start": "1"}
    )
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"
    assert response.status_code == 200
    assert response.json() == [{"name": "worker", "value": 1.23}]


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_intercept_and_process_worker_configuration_with_token(
    client, set_HIREFIRE_TOKEN
):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.get(
        "/hirefire", headers={"x-request-start": "1", "hirefire-token": HIREFIRE_TOKEN}
    )
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"
    assert response.status_code == 200
    assert response.json() == [{"name": "worker", "value": 1.23}]
