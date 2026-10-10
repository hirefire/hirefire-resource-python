import threading
from collections.abc import Callable, Hashable
from typing import TYPE_CHECKING

from hirefire_resource.log import safe_log

if TYPE_CHECKING:
    from hirefire_resource.configuration import Configuration


class Once:
    LIMIT = 256

    def __init__(self, configuration: "Configuration") -> None:
        self._configuration = configuration
        self._mutex = threading.Lock()
        self._seen: dict[str, dict[Hashable, bool]] = {}

    def log(
        self, level: str, kind: str, message: Callable[[], str], key: Hashable = None
    ) -> None:
        with self._mutex:
            seen = self._seen.setdefault(kind, {})
            if key in seen:
                return
            while len(seen) >= self.LIMIT:
                del seen[next(iter(seen))]
            seen[key] = True
        safe_log(self._configuration.logger, level, message())

    def unlock_after_fork(self) -> None:
        self._mutex = threading.Lock()
