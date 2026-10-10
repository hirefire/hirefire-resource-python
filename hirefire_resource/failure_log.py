import time
from typing import TYPE_CHECKING

from hirefire_resource.log import format_error, safe_log

if TYPE_CHECKING:
    from hirefire_resource.configuration import Configuration


class FailureLog:
    INTERVAL = 60

    def __init__(self, label: str, configuration: "Configuration") -> None:
        self._label = label
        self._configuration = configuration
        self.count = 0
        self._logged_at: float | None = None

    def failed(self, error: BaseException) -> None:
        self.record(f"{self._label} error: {format_error(error)}")

    def record(self, message: str) -> None:
        self.count += 1
        now = time.monotonic()
        if self._logged_at is not None and now - self._logged_at < self.INTERVAL:
            return

        self._logged_at = now
        attempts = f" ({self.count} failed attempts in a row)" if self.count > 1 else ""
        self._log("error", f"{message}{attempts}")

    def recovered(self) -> None:
        if self.count > 1:
            self._log(
                "info", f"{self._label} recovered after {self.count} failed attempts."
            )
        self.count = 0
        self._logged_at = None

    def _log(self, level: str, message: str) -> None:
        safe_log(self._configuration.logger, level, f"[HireFire] {message}")
