from hirefire_resource.configuration import Configuration
from hirefire_resource.web import Web
from hirefire_resource.worker import Worker


def test_web():
    config = Configuration()
    config.dyno("web")
    assert isinstance(config.web, Web)


def test_worker():
    config = Configuration()
    config.dyno("worker", lambda: 1.23)
    assert isinstance(config.workers[0], Worker)
    assert len(config.workers) == 1
