import json
import logging
import os
import sys
import threading

root_handlers_before = len(logging.getLogger().handlers)
hooks_before = None

from hirefire_resource import HireFire  # noqa: E402

logger = logging.getLogger("hirefire_resource")
host_level = logging.WARNING
logger.setLevel(host_level)
level_before_reset = logger.level
HireFire.reset()
result = {
    "threads_after_import": threading.active_count(),
    "logger_handlers": [
        f"{type(handler).__name__}({getattr(handler.stream, 'name', '?')})"
        for handler in logger.handlers
    ],
    "logger_propagate": logger.propagate,
    "level_the_host_set": logging.getLevelName(level_before_reset),
    "level_after_HireFire_reset": logging.getLevelName(logger.level),
    "fork_hooks_installed": HireFire._fork_hooks_installed,
    "modules_loaded": sorted(
        name for name in sys.modules if name.startswith("hirefire_resource")
    ),
    "configure_without_with_returns": type(HireFire.configure()).__name__,
    "token_env": bool(os.environ.get("HIREFIRE_TOKEN")),
}
print(json.dumps(result, indent=1))
