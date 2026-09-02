from importlib.metadata import PackageNotFoundError, metadata

VERSION: str
try:
    VERSION = metadata("hirefire-resource").get("Version") or "unknown"
except PackageNotFoundError:
    VERSION = "unknown"
