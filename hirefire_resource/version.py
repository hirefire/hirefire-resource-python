from importlib.metadata import PackageNotFoundError, metadata
from typing import Any

VERSION: str
try:
    # importlib.metadata's PackageMetadata stub omits .get(); the runtime object is
    # email.message.Message-backed and supports it. Read it as Any to keep the call.
    _metadata: Any = metadata("hirefire-resource")
    VERSION = _metadata.get("Version")
except PackageNotFoundError:
    VERSION = "unknown"
