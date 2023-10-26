import pytest

from hirefire_resource.configuration import Configuration, InvalidPlatformError
from hirefire_resource.web_dispatcher import WebDispatcher
from hirefire_resource.worker_server import WorkerServer
from tests.helpers import PLATFORM, TOKEN


def test_platform():
    for p in [PLATFORM]:
        config = Configuration(p)
        assert config.platform == p


def test_platform_invalid():
    with pytest.raises(InvalidPlatformError):
        Configuration("whoami")


def test_dispatch_web():
    config = Configuration(PLATFORM).dispatch(TOKEN)
    assert isinstance(config.web_dispatcher, WebDispatcher)


def test_serve_worker():
    config = Configuration(PLATFORM).serve(TOKEN, lambda: 1.23)
    assert isinstance(config.worker_servers._servers[0], WorkerServer)
    assert len(config.worker_servers._servers) == 1
