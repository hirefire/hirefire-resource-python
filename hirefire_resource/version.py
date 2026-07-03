from importlib.metadata import PackageNotFoundError, metadata
from typing import Any

VERSION: str
try:
    _metadata: Any = metadata("hirefire-resource")
    VERSION = _metadata.get("Version")
except PackageNotFoundError:
    VERSION = "unknown"
