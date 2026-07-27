from collections.abc import Mapping
from typing import Any

from hirefire_resource.middleware import present_header


def request_start_from_environ(environ: Mapping[str, Any]) -> str | None:
    return present_header(environ.get("HTTP_X_REQUEST_START")) or present_header(
        environ.get("HTTP_X_QUEUE_START")
    )
