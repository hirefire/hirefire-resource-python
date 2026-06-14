import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from quart import Quart

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.middleware.asgi.quart import HireFireMiddleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa: F401

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
async def test_pass_through_without_web(set_HIREFIRE_TOKEN, client):
    response = await client.get("/any", headers={"X-Request-Start": "1700000000000"})
    assert response.status_code == 200
    assert (await response.get_data(as_text=True)) == "DEFAULT"
    assert HireFire.configuration.buffer.flush()["web"] == {}


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_samples_web_and_starts_dispatcher(set_HIREFIRE_TOKEN, client):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        response = await client.get(
            "/any", headers={"X-Request-Start": str(int(time.time() * 1000 - 5))}
        )
        assert response.status_code == 200
        assert (await response.get_data(as_text=True)) == "DEFAULT"
        assert HireFire.configuration.buffer.flush()["web"] == {int(time.time()): [5]}
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_info_path_passes_through(set_HIREFIRE_TOKEN, client):
    with patch.object(Dispatcher, "start"):
        with HireFire.configure() as config:
            config.dyno("worker", lambda: 1.23)
        response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")
        assert response.status_code == 200
        assert (await response.get_data(as_text=True)) == "DEFAULT"
