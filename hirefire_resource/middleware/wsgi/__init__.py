from collections.abc import Mapping
from typing import Any


def request_start_from_environ(environ: Mapping[str, Any]) -> str | None:
    return environ.get("HTTP_X_REQUEST_START") or environ.get("HTTP_X_QUEUE_START")
