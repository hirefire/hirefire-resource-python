import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from quart import Quart

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.asgi.quart import HireFireMiddleware
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

app = Quart(__name__)
app.asgi_app = HireFireMiddleware(app)
app.config["TESTING"] = True


async def catch_all(path):
    return "DEFAULT", 200


app.add_url_rule("/<path:path>", "catch_all", catch_all)


@pytest.fixture(autouse=True)
def setup():
    HireFire.configuration = Configuration()
    yield


@pytest.fixture
def client():
    return app.test_client()


async def measure_queue_metric():
    return 1.23


@pytest.mark.asyncio
async def test_pass_through_without_HIREFIRE_TOKEN(client):
    with HireFire.configure() as config:
        config.dyno("web")
        config.dyno("worker", measure_queue_metric)
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = await client.get("/any", headers={"x-request-start": "1"})
        assert response.status_code == 200
        assert (await response.get_data(as_text=True)) == "DEFAULT"
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_pass_through_without_configuration(set_HIREFIRE_TOKEN, client):
    HireFire.configuration = Configuration()
    response = await client.get("/any", headers={"X-Request-Start": "1"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert (await response.get_data(as_text=True)) == "DEFAULT"


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_pass_through_and_process_web_configuration(set_HIREFIRE_TOKEN, client):
    with HireFire.configure() as config:
        config.dyno("web")
    with patch.object(HireFire.configuration.web, "start_dispatcher") as mock_start:
        response = await client.get(
            "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert (await response.get_data(as_text=True)) == "DEFAULT"
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert HireFire.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_intercept_and_process_worker_configuration(set_HIREFIRE_TOKEN, client):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = await client.get(
        f"/hirefire/{HIREFIRE_TOKEN}/info", headers={"X-Request-Start": "1"}
    )
    assert response.status_code == 200
    assert await response.get_json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_intercept_and_process_worker_configuration_with_token(
    set_HIREFIRE_TOKEN, client
):
    with HireFire.configure() as config:
        config.dyno("worker", measure_queue_metric)
    response = await client.get(
        f"/hirefire", headers={"X-Request-Start": "1", "HireFire-Token": HIREFIRE_TOKEN}
    )
    assert response.status_code == 200
    assert await response.get_json() == [{"name": "worker", "value": 1.23}]
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert response.headers["hirefire-resource"] == f"Python-{VERSION}"
