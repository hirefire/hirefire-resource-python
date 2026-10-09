import os

from common import boot
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from hirefire_resource.middleware.asgi.starlette import HireFireMiddleware


async def index(request):
    return PlainTextResponse(f"pid={os.getpid()}")


app = Starlette(routes=[Route("/", index)], middleware=[Middleware(HireFireMiddleware)])
boot()
