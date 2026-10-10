from typing import Any

from hirefire_resource.middleware import asgi

__all__ = ["HireFireMiddleware"]


class HireFireMiddleware(asgi.HireFireMiddleware):
    def __init__(self, inner: Any) -> None:
        super().__init__(inner)
