from importlib.metadata import PackageNotFoundError, version

VERSION: str
try:
    VERSION = version("hirefire-resource")
except PackageNotFoundError:
    VERSION = "unknown"
