from typing import reveal_type

from hirefire_resource import HireFire, __version__
from hirefire_resource.configuration import Configuration
from hirefire_resource.errors import MissingQueueError
from hirefire_resource.middleware.asgi.starlette import (
    HireFireMiddleware as AsgiMiddleware,
)
from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware as WsgiMiddleware


def queue_size() -> int:
    return 3


async def queue_latency() -> float:
    return 1.5


with HireFire.configure() as config:
    reveal_type(config)
    config.token = "token"
    config.dyno("worker", queue_size)
    config.dyno("worker_latency", queue_latency)
    config.dyno("web")
    config.dyno("worker_lambda", lambda: 1)

booted: Configuration = HireFire.boot()
HireFire.reset()
version: str = __version__
error = MissingQueueError()
reveal_type(AsgiMiddleware)
reveal_type(WsgiMiddleware)
config.dyno("bad", "not callable")
config.dyno(123)
