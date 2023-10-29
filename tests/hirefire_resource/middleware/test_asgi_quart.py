import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from quart import Quart

from hirefire_resource import Configuration, Resource
from hirefire_resource.middleware.asgi import NotConfigured
from hirefire_resource.middleware.asgi.quart import Middleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa

app = Quart(__name__)
app.asgi_app = Middleware(app)
app.config["TESTING"] = True


@app.route("/<path:path>")
async def catch_all(path):
    return "DEFAULT", 200


@pytest.fixture
def client():
    return app.test_client()


@pytest.mark.asyncio
async def test_without_configuration(set_HIREFIRE_TOKEN, client):
    Resource.configuration = None
    with pytest.raises(NotConfigured):
        await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info")


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_without_web_and_worker(set_HIREFIRE_TOKEN, client):
    Resource.configuration = Configuration()
    headers = {"X-Request-Start": str(int(time.time() * 1000 - 5))}
    response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
    assert response.status_code == 200
    assert await response.get_json() == []
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "must-revalidate, private, max-age=0"
    assert Resource.configuration.web is None


@freeze_time("2000-01-01 00:00:00")
@pytest.mark.asyncio
async def test_web_and_worker(set_HIREFIRE_TOKEN, client):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    with patch.object(Resource.configuration.web, "start") as mock_start:
        headers = {"X-Request-Start": str(int(time.time() * 1000 - 5))}
        response = await client.get(f"/hirefire/{HIREFIRE_TOKEN}/info", headers=headers)
        assert response.status_code == 200
        assert await response.get_json() == [{"name": "worker", "value": 1.23}]
        assert response.headers["content-type"] == "application/json"
        assert (
            response.headers["cache-control"] == "must-revalidate, private, max-age=0"
        )
        assert Resource.configuration.web._buffer == {946684800: [5]}
        mock_start.assert_called()


@pytest.mark.asyncio
async def test_default(set_HIREFIRE_TOKEN, client):
    Resource.configuration = Configuration().dyno("web").dyno("worker", lambda: 1.23)
    response = await client.get(f"/hirefire/wrong/info")
    assert response.status_code == 200
    assert (await response.get_data(as_text=True)) == "DEFAULT"
    assert response.headers["content-type"] == "text/html; charset=utf-8"
