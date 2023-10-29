import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from freezegun import freeze_time

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware.asgi import NotConfigured
from hirefire_resource.middleware.asgi.starlette import Middleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

app = FastAPI()

# Apply the middleware
app.add_middleware(Middleware)


@app.get("/hirefire/{token}/info")
async def hirefire_info(request: Request, token: str):
    # Here you would have the logic that you want to test
    # For example, if token matches the HIREFIRE_TOKEN, return some data
    if token == HIREFIRE_TOKEN:
        return JSONResponse(content=[{"name": "worker", "value": 1.23}])

    return Response(content="DEFAULT", media_type="text/plain")


# The rest of your endpoints would be defined here

# Create a TestClient using the FastAPI app
client = TestClient(app)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_without_configuration(client, set_HIREFIRE_TOKEN):
    Resource.configuration = None
    with pytest.raises(NotConfigured):
        await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_without_web_and_worker(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration()
    headers = {"X-Request-Start": str(int(time.time() * 1000 - 5))}
    response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert Resource.configuration.web is None


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_web_and_worker(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(Resource.configuration.web, "start") as mock_start:
        headers = {"X-Request-Start": str(int(time.time() * 1000 - 5))}
        response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
        assert response.status_code == 200
        assert response.json() == [{"name": "worker", "value": 1.23}]
        assert response.headers["content-type"] == "application/json"
        assert (
            response.headers["cache-control"] == "must-revalidate, private, max-age=0"
        )
        assert {946684800: [5]} == Resource.configuration.web._buffer
        mock_start.assert_called()


@pytest.mark.asyncio
async def test_default(client, set_HIREFIRE_TOKEN):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    response = client.get("/hirefire/wrong/info")
    assert response.status_code == 200
    assert response.text == "DEFAULT"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
