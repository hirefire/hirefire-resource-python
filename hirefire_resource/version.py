from importlib.metadata import PackageNotFoundError, metadata

try:
    VERSION = metadata("hirefire-resource").get("Version")
except PackageNotFoundError:
    VERSION = "unknown"
