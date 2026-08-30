import os
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
            The configuration to declare processes on.

        Example::

            with HireFire.configure() as config:
                config.dyno("worker", sampler)
        """
        yield cls.configuration
        cls._start_if_token()

    @classmethod
    def boot(cls) -> Configuration:
        """Starts HireFire with no local source declarations.

        Equivalent to :meth:`configure` with an empty block. Use for zero-config
        installs that rely on always-on request queue time and CPU, plus lease plan
        macros for job-queue metrics. Full :meth:`configure` remains available for
        local job-queue samplers via :meth:`Configuration.dyno`.

        Returns:
            The configuration.

        Example::

            HireFire.boot()
        """
        with cls.configure():
            pass
        return cls.configuration

    @classmethod
    def reset(cls) -> None:
        """Stops any running dispatcher and replaces the configuration with a fresh one.

        Mainly for tests and reconfiguration between runs.
        """
        cls.configuration.stop_dispatcher()
        cls.configuration = Configuration()

    @classmethod
    def after_fork_in_parent(cls) -> None:
        """Called in the parent after fork.

        For prefork web masters, stops the dispatcher without a final flush so the
        master does not claim empty web liveness under the workers' process name.
        Children restart via :meth:`after_fork_in_child` or middleware.

        Job-only parents are left running: stopping them would kill fleet job
        metrics after the first job fork, and middleware cannot restart a pure
        worker.
        """
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
        """Called in the child after fork.

        Prefork web workers restart reporting when a token is present.
        Fork-per-job children abandon inherited dispatcher state so they do not
        enter the lease race and do not flush the parent's buffer.
        """
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
        """Installs ``os.register_at_fork`` hooks so prefork clusters behave correctly.

        The parent stops reporting (a prefork master must not keep empty web
        liveness), and each child restarts without needing middleware. Safe to
        call more than once.
        """
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
