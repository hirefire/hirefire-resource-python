import logging
import os
import time

from fake_server import FakeServer

IDENTITY_ENV = [
    "DYNO",
    "HIREFIRE_SERVICE_NAME",
    "RENDER_SERVICE_NAME",
    "RENDER_SERVICE_TYPE",
    "RENDER",
    "RENDER_CPU_COUNT",
    "HIREFIRE_VERBOSE",
    "HIREFIRE_TOKEN",
    "HIREFIRE_DATA_URL",
]


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.levelname} {record.getMessage()}")


def capture_logger(name="tools.evidence"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = Lines()
    logger.handlers = [handler]
    return logger, handler.lines


def boot(handler=None, env=None, configure=None, token="evidence-token"):
    server = FakeServer(handler)
    for key in IDENTITY_ENV:
        os.environ.pop(key, None)
    os.environ.update(env or {})
    if token is not None:
        os.environ["HIREFIRE_TOKEN"] = token
    os.environ.setdefault("HIREFIRE_DATA_URL", server.url)
    from hirefire_resource import HireFire

    logger, lines = capture_logger()
    with HireFire.configure() as config:
        config.logger = logger
        if configure:
            configure(config)
    return HireFire, server, lines


def wait_for(predicate, timeout=5.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False
