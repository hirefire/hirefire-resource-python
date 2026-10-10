from importlib.metadata import PackageNotFoundError, version

UNKNOWN = "unknown"

VERSION: str
try:
    VERSION = version("hirefire-resource")
except PackageNotFoundError:
    VERSION = UNKNOWN
