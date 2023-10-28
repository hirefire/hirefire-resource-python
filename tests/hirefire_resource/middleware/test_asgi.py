import json
import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from hirefire_resource.configuration import Configuration
from hirefire_resource.middleware.asgi import BaseMiddleware, NotConfigured, RequestInfo
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa


@pytest.fixture
def build_config():
    return Configuration()


async def call(config, path="", headers={}):
    return await BaseMiddleware(config).process_request(RequestInfo(path, headers))


@pytest.mark.asyncio
async def test_call_default(build_config):
    assert None == await call(build_config)


@pytest.mark.asyncio
async def test_not_configured():
    with pytest.raises(NotConfigured):
        await call(None)


# @TODO add another response test but instead of lambda, we should pass an async (coroutine) to make sure
# it is properly awaited.
@pytest.mark.asyncio
async def test_info_response(set_HIREFIRE_TOKEN, build_config):
    config = build_config.dyno("worker", lambda: 1.23)
    response = await call(config, f"/hirefire/{HIREFIRE_TOKEN}/info")
    headers = {
        "content-type": "application/json",
        "cache-control": "must-revalidate, private, max-age=0",
    }
    assert (200, headers, json.dumps([{"name": "worker", "value": 1.23}])) == response


@pytest.mark.asyncio
async def test_process_request_queue_time(set_HIREFIRE_TOKEN, build_config):
    config = build_config.dyno("web")
    with patch.object(config.web, "start") as mock_start:
        for second, request_start in [[0, 500], [0, 1_000], [1, 1_500]]:
            with freeze_time(f"2000-01-01 00:00:0{second}"):
                current_time = int(time.time() * 1_000)
                response = await call(
                    config,
                    "/",
                    {"HTTP_X_REQUEST_START": (current_time - request_start)},
                )
                assert None == response
                mock_start.assert_called()
    assert {946684800: [500, 1000], 946684801: [1500]} == config.web._buffer


@pytest.mark.asyncio
async def test_process_request_queue_time_config(set_HIREFIRE_TOKEN, build_config):
    config = build_config
    response = await call(config, "/", {"HTTP_X_REQUEST_START": time.time()})
    assert None == response
    assert None == config.web


@pytest.mark.asyncio
async def test_process_request_queue_time_missing_headers(
    set_HIREFIRE_TOKEN, build_config
):
    config = build_config.dyno("web")
    with patch.object(config.web, "start") as mock_start:
        response = await call(config, "/", {})
        assert None == response
        assert {} == config.web._buffer
        mock_start.assert_not_called()
