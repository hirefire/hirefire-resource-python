import time
from datetime import datetime
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from starlette.responses import Response
from starlette.testclient import TestClient

from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware import NotConfigured
from hirefire_resource.middleware.asgi.django import Middleware
from hirefire_resource.resource import Resource
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


# Wrap the simple ASGI app with the Middleware for testing
asgi_app = Middleware(app)


@pytest.fixture
def async_client():
    with TestClient(asgi_app) as client:
        yield client


@pytest.mark.asyncio
async def test_without_configuration(async_client):
    Resource.configuration = None
    with pytest.raises(NotConfigured):
        async_client.get("/")


@pytest.mark.asyncio
async def test_without_web_and_worker(async_client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration()
    path = f"/hirefire/{HIREFIRE_TOKEN}/info"
    response = async_client.get(
        path, headers={"x-request-start": str(int(time.time() * 1000 - 5))}
    )
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.status_code == 200
    assert response.json() == []
    assert Resource.configuration.web is None


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_web_and_worker(async_client, set_HIREFIRE_TOKEN):
    # Configure HireFire with web and worker dynos
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)

    # Use a patch to mock the 'start' method on the web dyno
    with patch.object(Resource.configuration.web, "start") as mock_start:
        path = f"/hirefire/{HIREFIRE_TOKEN}/info"
        # Make a synchronous call to the test client
        response = async_client.get(
            path, headers={"x-request-start": str(int(time.time() * 1000 - 5))}
        )

        # Verify the response
        assert response.headers["content-type"] == "application/json"
        assert (
            response.headers["cache-control"] == "must-revalidate, private, max-age=0"
        )
        assert response.status_code == 200
        assert response.json() == [{"name": "worker", "value": 1.23}]

        # Use the frozen datetime to get the timestamp
        frozen_time = datetime.now().timestamp()
        assert Resource.configuration.web._buffer == {int(frozen_time): [5]}

        # Assert that the 'start' method was called
        mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_default(async_client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    path = f"/hirefire/wrong/info"
    response = async_client.get(path)
    assert response.status_code == 200
    assert response.content == b"DEFAULT"
    assert response.headers["content-type"] == "text/html; charset=utf-8"
