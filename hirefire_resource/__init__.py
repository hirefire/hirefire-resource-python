"""
HireFire is the oldest and a leading autoscaling service for
applications hosted on Heroku. Since 2011, we've assisted more than
1,000 companies in autoscaling upwards of 5,000 applications,
involving over 10,000 dynos.

This package streamlines the integration of HireFire with Python
applications running on Heroku, offering companies substantial cost
savings while maintaining optimal performance.
"""

from importlib.metadata import PackageNotFoundError, metadata

try:
    _metadata = metadata("hirefire-resource")

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

from hirefire_resource.resource import Resource

Resource  # export
