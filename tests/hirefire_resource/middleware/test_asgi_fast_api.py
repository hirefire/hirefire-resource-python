import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.routing import APIRouter
from fastapi.testclient import TestClient
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.asgi.starlette import HireFireMiddleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa: F401

app = FastAPI()
app.add_middleware(HireFireMiddleware)

router = APIRouter()


@router.api_route("/{path:path}", methods=["GET"])
async def catch_all(request: Request):
    return Response(content="DEFAULT", media_type="text/plain")


app.include_router(router)


@pytest.fixture
def client():
    return TestClient(app)


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
def test_info_path_passes_through(client, set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("worker", lambda: 1.23)
        response = client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")
        assert response.status_code == 200
        assert response.content == b"DEFAULT"
