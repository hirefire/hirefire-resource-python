import os
import threading
from typing import TYPE_CHECKING

from hirefire_resource import plan, version
from hirefire_resource.dispatcher.session import Session
from hirefire_resource.log import safe_log

if TYPE_CHECKING:
    import logging

    from hirefire_resource.configuration import Configuration


class Dispatcher:
    JOIN_TIMEOUT = 5

    def __init__(self, configuration: "Configuration") -> None:
        self._configuration = configuration
        self._mutex = threading.Lock()
        self._session: Session | None = None
        self._retired: Session | None = None
        self._pid: int | None = None
        self._stopping = False

    def start(self) -> bool:
        if self._healthy():
            return False
        if self._pid is not None and self._pid != os.getpid():
            self.abandon_inherited_state()

        try:
            with self._mutex:
                if self._stopping or self._healthy():
                    return False

                if self._session is not None:
                    self._session.halt()
                retired = self._session or self._retired
                self._session = Session(
                    self._configuration,
                    after=retired.sample_thread if retired is not None else None,
                ).start()
                self._retired = None
                self._pid = os.getpid()
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Could not start dispatcher: {error}",
            )
            return False

        safe_log(self._logger(), "info", "[HireFire] Starting dispatcher.")
        self._warn_unknown_version_once()
        return True

    def stop(self, flush: bool = True) -> bool:
        with self._mutex:
            session = self._session
            if session is None or not session.live():
                return False

            self._stopping = True
            self._retired = session
            self._session = None

        try:
            session.halt(handoff=flush)
            if not flush:
                self._configuration.buffer.discard()
            elif session.join(self.JOIN_TIMEOUT):
                session.flush()
                session.close()
            else:
                session.abandon()
                safe_log(
                    self._logger(),
                    "warning",
                    "[HireFire] The dispatch loop did not stop within "
                    f"{self.JOIN_TIMEOUT} seconds. The final flush is skipped.",
                )

            safe_log(self._logger(), "info", "[HireFire] Dispatcher stopped.")
            return True
        finally:
            with self._mutex:
                self._stopping = False

    def running(self) -> bool:
        with self._mutex:
            return self._healthy()

    def abandon_inherited_state(self) -> None:
        try:
            self._mutex = threading.Lock()
            session = self._session or self._retired
            self._session = None
            self._retired = None
            self._pid = None
            self._stopping = False
            if session is not None:
                session.discard_inherited()
            self._configuration.buffer.reinit_after_fork()
            plan.reinit_macros_after_fork(self._logger())
            self._configuration.reset_after_fork()
        except Exception as error:
            safe_log(
                self._logger(),
                "error",
                f"[HireFire] Could not abandon inherited dispatcher state: {error}",
            )

    def _warn_unknown_version_once(self) -> None:
        if version.VERSION != version.UNKNOWN:
            return

        self._configuration.once.log(
            "warning",
            "unknown_version",
            lambda: (
                "[HireFire] The version of hirefire-resource is unknown, because the "
                "metadata of the package (its dist-info directory) is not installed. "
                "HireFire treats a client without a version as a legacy client. "
                "Install the package with its metadata."
            ),
        )

    def _healthy(self) -> bool:
        session = self._session
        return self._pid == os.getpid() and session is not None and session.alive()

    def _logger(self) -> "logging.Logger":
        return self._configuration.logger
