from importlib.metadata import PackageNotFoundError, metadata
from typing import Any

try:
    # importlib.metadata's PackageMetadata stub omits .get(); the runtime object is
    # email.message.Message-backed and supports it. Read it as Any to keep the call.
    _metadata: Any = metadata("hirefire-resource")

    __version__ = _metadata.get("Version")
    __author__ = _metadata.get("Author")
    __contact__ = _metadata.get("Author-email")
    __homepage__ = _metadata.get("Home-page")
    __keywords__ = _metadata.get("Keywords", "").split(", ")
    __docformat__ = "google"
except PackageNotFoundError:
    __version__ = "unknown"
    __author__ = "unknown"
    __contact__ = "unknown"
    __homepage__ = "unknown"
    __keywords__ = "unknown"
    __docformat__ = "unknown"

from hirefire_resource.hirefire import HireFire  # noqa

__all__ = ["HireFire"]
