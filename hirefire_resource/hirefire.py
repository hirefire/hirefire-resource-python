import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from hirefire_resource.configuration import Configuration
from hirefire_resource.log import safe_log


class HireFire:
    """HireFire singleton entrypoint: configure processes and report metrics.

    Attributes:
        configuration: The process-wide shared configuration.
    """

    configuration: Configuration = Configuration()
    _fork_hooks_installed: bool = False

    @classmethod
    @contextmanager
    def configure(cls) -> Iterator[Configuration]:
        """Configures HireFire and starts reporting metrics when a token is present.

        Yields the configuration object so each process can declare local sources
        (see :meth:`Configuration.dyno`). Zero-config installs can use :meth:`boot`
        instead.

        On exit, the dispatcher starts automatically when a token is present, set
        in code (``config.token = ...``) or via the ``HIREFIRE_TOKEN`` environment
        variable. With no token the app runs normally and reports nothing, so it is
        safe to leave configured in every environment.

        Configuration is additive: a later :meth:`configure` may add local job-queue
        samplers without :meth:`reset`. Lease race entry and the job-queue loop are
        re-evaluated so late job-queue samplers take effect.

        Yields:
            Configuration: The configuration to declare processes on.
        """
        yield cls.configuration
        cls._start_if_token()

    @classmethod
    def boot(cls) -> Configuration:
        """Starts HireFire with no local source declarations.

        Equivalent to :meth:`configure` with an empty block. Use for zero-config
        installs that rely on always-on request queue time and CPU, plus lease plan
        macros for job-queue metrics.

        Returns:
            Configuration: The configuration.
        """
        with cls.configure():
            pass
        return cls.configuration

    @classmethod
    def reset(cls) -> None:
        """Stops any running dispatcher and replaces the configuration with a fresh one.

        Mainly for tests and reconfiguration between runs.
        """
        if cls.configuration is not None:
            cls.configuration.stop_dispatcher()
        cls.configuration = Configuration()

    @classmethod
    def after_fork_in_parent(cls) -> None:
        """Parent after-fork: stop without flush when prefork web handoff is armed."""
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
        """Child after-fork three-way handoff.

        Prefork web handoff with token: start + ensure. Handoff without token: no-op.
        Otherwise abandon inherited state.
        """
        try:
            cls.configuration._reinit_locks_after_fork()
            # Reinit Celery macro caches only if the macro is already loaded.
            # Importing hirefire_resource.macro.celery would `import celery` and
            # poison enter_race / library_loaded for non-worker children.
            celery_macro = sys.modules.get("hirefire_resource.macro.celery")
            if celery_macro is not None:
                try:
                    celery_macro._reinit_after_fork()
                except Exception:
                    pass

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
