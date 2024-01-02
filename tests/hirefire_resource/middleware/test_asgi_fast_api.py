import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.routing import APIRouter
from fastapi.testclient import TestClient
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.asgi.starlette import HireFireMiddleware
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

app = FastAPI()

app.add_middleware(HireFireMiddleware)

client = TestClient(app)

router = APIRouter()


@router.api_route("/{path:path}", methods=["GET"])
async def catch_all(request: Request):
    return Response(content="DEFAULT", media_type="text/plain")


app.include_router(router)


@pytest.fixture(autouse=True)
def setup():
    HireFire.configuration = Configuration()
    yield


@pytest.fixture
def client():
    return TestClient(app)


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
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        mock_start.assert_not_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_pass_through_without_configuration(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    response = client.get("/", headers={"X-Request-Start": "1"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == b"DEFAULT"


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_pass_through_and_process_web_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("web")
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = client.get(
            "/", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.content == b"DEFAULT"
        assert {946684800: [5]} == HireFire.configuration.web._buffer
        mock_start.assert_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_intercept_and_process_worker_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.get(
        f"/hirefire/{HIREFIRE_TOKEN}/info", headers={"X-Request-Start": "1"}
    )
    assert response.status_code == 200
    assert response.json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_intercept_and_process_worker_configuration_with_token(
    client, set_HIREFIRE_TOKEN
):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = client.get(
        f"/hirefire", headers={"HireFire-Token": HIREFIRE_TOKEN, "X-Request-Start": "1"}
    )
    assert response.status_code == 200
    assert response.json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"
