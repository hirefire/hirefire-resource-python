import json
import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware import NotConfigured, RequestInfo
from hirefire_resource.middleware.asgi import BaseMiddleware
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


async def call(config, path="/", headers={}):
    return await BaseMiddleware(config).process_request(RequestInfo(path, headers))


def worker_data():
    return 1.23


async def async_worker_data():
    return 1.23


@pytest.mark.asyncio
async def test_pass():
    assert None == await call(Configuration())


@pytest.mark.asyncio
async def test_not_configured_error():
    with pytest.raises(NotConfigured):
        await call(None)


@pytest.mark.asyncio
async def test_web(set_HIREFIRE_TOKEN):
    config = Configuration().dyno("web")
    with patch.object(config.web, "start") as mock_start:
        for second, request_start in [[0, 500], [0, 1_000], [1, 1_500]]:
            with freeze_time(f"2000-01-01 00:00:0{second}"):
                current_time = int(time.time() * 1_000)
                response = await call(config, "/", (current_time - request_start))
                assert None == response
        mock_start.assert_called()
    assert {946684800: [500, 1000], 946684801: [1500]} == config.web._buffer


@pytest.mark.asyncio
async def test_web_without_request_start_time(set_HIREFIRE_TOKEN):
    config = Configuration().dyno("web")
    with patch.object(config.web, "start") as mock_start:
        response = await call(config, "/", None)
        assert None == response
        assert {} == config.web._buffer
        mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_worker(set_HIREFIRE_TOKEN):
    config = Configuration().dyno("worker", worker_data)
    response = await call(config, f"/hirefire/{HIREFIRE_TOKEN}/info")
    headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    }
    assert (200, headers, json.dumps([{"name": "worker", "value": 1.23}])) == response


@pytest.mark.asyncio
async def test_worker_async(set_HIREFIRE_TOKEN):
    config = Configuration().dyno("worker", async_worker_data)
    response = await call(config, f"/hirefire/{HIREFIRE_TOKEN}/info")
    headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    }
    assert (200, headers, json.dumps([{"name": "worker", "value": 1.23}])) == response
