from hirefire_resource.configuration import Configuration
from hirefire_resource.web import Web


def test_web():
    config = Configuration()
    config.dyno("web")
    assert isinstance(config.web, Web)


def test_workers():
    config = Configuration()
    config.dyno("worker", lambda: 1.23)
    config.dyno("mailer", lambda: 2.46)
    assert len(config.workers) == 2
    assert config.workers[0].name == "worker"
    assert config.workers[0].value() == 1.23
    assert config.workers[1].name == "mailer"
    assert config.workers[1].value() == 2.46
