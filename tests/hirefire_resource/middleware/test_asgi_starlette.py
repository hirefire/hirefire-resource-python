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
from hirefire_resource.middleware.asgi.starlette import HireFireMiddleware
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


async def catch_all(request):
    return PlainTextResponse("DEFAULT")


routes = [Route("/{path:path}", catch_all)]
app = Starlette(routes=routes)
app = HireFireMiddleware(app)


@pytest.fixture(autouse=True)
def setup():
    HireFire.configuration = Configuration()
    yield


@pytest.fixture
def client():
    return httpx.AsyncClient(app=app, base_url="http://test")


async def measure_queue_metric():
    return 1.23


@pytest.mark.asyncio
async def test_pass_through_without_HIREFIRE_TOKEN(client):
    with HireFire.configure() as config:
        config.dyno("web")
        config.dyno("worker", measure_queue_metric)
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = await client.get("/", headers={"x-request-start": "1"})
        assert response.status_code == 200
        assert response.text == "DEFAULT"
        assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
        mock_start.assert_not_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_pass_through_without_configuration(client, set_HIREFIRE_TOKEN):
    HireFire.configuration = Configuration()
    response = await client.get("/", headers={"X-Request-Start": "1"})
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert response.text == "DEFAULT"


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_pass_through_and_process_web_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("web")
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = await client.get(
            "/", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
        assert response.text == "DEFAULT"
        assert HireFire.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_intercept_and_process_worker_configuration(client, set_HIREFIRE_TOKEN):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = await client.get(
        f"/hirefire/{HIREFIRE_TOKEN}/info", headers={"X-Request-Start": "1"}
    )
    assert response.status_code == 200
    assert response.json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"


@pytest.mark.asyncio
@freeze_time("2000-01-01 00:00:00")
async def test_intercept_and_process_worker_configuration_with_token(
    client, set_HIREFIRE_TOKEN
):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = await client.get(
        f"/hirefire", headers={"X-Request-Start": "1", "HireFire-Token": HIREFIRE_TOKEN}
    )
    assert response.status_code == 200
    assert response.json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"
