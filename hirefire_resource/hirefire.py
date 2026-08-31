import os
from collections.abc import Iterator
from contextlib import contextmanager

from hirefire_resource.configuration import Configuration
from hirefire_resource.log import safe_log


class HireFire:
    configuration: Configuration = Configuration()
    _fork_hooks_installed: bool = False

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
        cls.configuration = Configuration()

    @classmethod
    def after_fork_in_parent(cls) -> None:
        try:
            if not cls.configuration.prefork_web_handoff():
                return
            cls.configuration.stop_dispatcher(flush=False)
        except Exception as error:
            safe_log(
                cls.configuration.logger,
                "error",
                f"[HireFire] After-fork parent stop failed: {error}",
            )

    @classmethod
    def after_fork_in_child(cls) -> None:
        try:
            cls.configuration._reinit_locks_after_fork()

            if cls.configuration.prefork_web_handoff():
                if not cls.configuration.token:
                    return
                cls.configuration.dispatcher.start()
                cls.configuration.dispatcher.ensure_job_queue_loop()
            else:
                cls.configuration.dispatcher.abandon_inherited_state()
        except Exception as error:
            safe_log(
                cls.configuration.logger,
                "error",
                f"[HireFire] After-fork restart failed: {error}",
            )

    @classmethod
    def _start_if_token(cls) -> None:
        if not cls.configuration.token:
            return
        cls.configuration.dispatcher.start()
        cls.configuration.dispatcher.ensure_job_queue_loop()

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


HireFire.install_fork_hooks()
