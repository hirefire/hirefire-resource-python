import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from hirefire_resource import host, identity, plan
from hirefire_resource.configuration import Configuration
from hirefire_resource.dispatcher.session import Session
from hirefire_resource.log import safe_log


class HireFire:
    configuration: Configuration = Configuration()
    _fork_hooks_installed: bool = False
    _handoff_lock = threading.Lock()
    _handed_over = False
    _handoff_watch: threading.Thread | None = None

    @classmethod
    @contextmanager
    def configure(cls) -> Iterator[Configuration]:
        yield cls.configuration
        cls._start_if_token()

    @classmethod
    def boot(cls) -> Configuration:
        with cls.configure():
            pass
        return cls.configuration

    @classmethod
    def reset(cls) -> None:
        cls.configuration.stop_dispatcher()
        with cls._handoff_lock:
            cls._handed_over = False
        cls.configuration = Configuration()

    @classmethod
    def after_fork_in_parent(cls) -> None:
        try:
            if not cls.configuration.prefork_web_handoff():
                return

            with cls._handoff_lock:
                cls.configuration.stop_dispatcher(flush=False)
                if hasattr(os, "waitid"):
                    cls._handed_over = True
                    cls._handoff_watch = cls._handoff_watch or cls._watch_handoff()
        except Exception as error:
            safe_log(
                cls.configuration.logger,
                "error",
                f"[HireFire] After-fork parent stop failed: {error}",
            )

    @classmethod
    def after_fork_in_child(cls) -> None:
        try:
            cls._handoff_lock = threading.Lock()
            cls._handed_over = False
            cls._handoff_watch = None
            configuration = cls.configuration
            configuration.unlock_after_fork()
            configuration.dispatcher.abandon_inherited_state()
            if configuration.prefork_web_handoff() and configuration.token:
                configuration.dispatcher.start()
        except Exception as error:
            safe_log(
                cls.configuration.logger,
                "error",
                f"[HireFire] After-fork restart failed: {error}",
            )

    @classmethod
    def install_fork_hooks(cls) -> None:
        if cls._fork_hooks_installed:
            return
        if not hasattr(os, "register_at_fork"):
            return
        cls._fork_hooks_installed = True
        os.register_at_fork(
            after_in_parent=cls.after_fork_in_parent,
            after_in_child=cls.after_fork_in_child,
        )

    @classmethod
    def _start_if_token(cls) -> None:
        if not cls.configuration.token:
            return
        plan.attach_macros(cls.configuration.logger)
        if identity.one_off():
            return
        if host.uwsgi_parent():
            cls.configuration.once.log(
                "warning",
                "uwsgi_parent",
                lambda: (
                    "[HireFire] uWSGI loaded the app in its parent process, so the "
                    "dispatcher starts on the first request of each worker. Run uWSGI "
                    "with --lazy-apps to start it when a worker boots. "
                    "uWSGI is not a tested server."
                ),
            )
            return
        cls.configuration.dispatcher.start()

    @classmethod
    def _watch_handoff(cls) -> threading.Thread:
        thread = threading.Thread(
            target=cls._wait_for_children, name="hirefire-handoff", daemon=True
        )
        thread.start()
        return thread

    @classmethod
    def _wait_for_children(cls) -> None:
        try:
            while not cls._handoff_settled():
                time.sleep(Session.TICK)
        except Exception as error:
            with cls._handoff_lock:
                cls._handed_over = False
                cls._handoff_watch = None
            safe_log(
                cls.configuration.logger,
                "error",
                f"[HireFire] After-fork resume failed: {error}",
            )

    @classmethod
    def _handoff_settled(cls) -> bool:
        with cls._handoff_lock:
            if cls._handed_over and not cls.configuration.dispatcher.running():
                if cls._children_left():
                    return False
                cls._start_if_token()
            cls._handed_over = False
            cls._handoff_watch = None
            return True

    @staticmethod
    def _children_left() -> bool:
        posix: Any = os
        try:
            posix.waitid(posix.P_ALL, 0, posix.WEXITED | posix.WNOHANG | posix.WNOWAIT)
        except ChildProcessError:
            return False
        return True


HireFire.install_fork_hooks()
