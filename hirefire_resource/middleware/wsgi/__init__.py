from collections.abc import Mapping
from typing import Any

from hirefire_resource.identity import presence


def request_start_from_environ(environ: Mapping[str, Any]) -> str | None:
    return presence(environ.get("HTTP_X_REQUEST_START")) or presence(
        environ.get("HTTP_X_QUEUE_START")
    )
