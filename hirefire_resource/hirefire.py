import os
from collections.abc import Iterator
from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class HireFire:
    """HireFire singleton entrypoint: configure processes and report metrics.

    Attributes:
        configuration: The process-wide shared configuration.
    """

    configuration: Configuration = Configuration()

    @classmethod
    @contextmanager
    def configure(cls) -> Iterator[Configuration]:
        """Configures HireFire and starts reporting metrics.

        Yields the configuration object so each process can declare what it tracks
        (see :meth:`Configuration.service` and :meth:`Configuration.dyno`).

        On exit, the dispatcher starts automatically when a token is present, set
        in code (``config.token = ...``) or via the ``HIREFIRE_TOKEN`` environment
        variable. With no token the app runs normally and reports nothing, so it is
        safe to leave configured in every environment.

        Yields:
            Configuration: The configuration to declare processes on.

        Examples:
            >>> with HireFire.configure() as config:
            ...     config.service("web", tracking="http")
            ...     config.service("worker", lambda: job_queue_latency("default"))
            ...     config.service("encoder", tracking="cpu")
        """
        yield cls.configuration
        if cls.configuration.token:
            cls.configuration.dispatcher.start()

    @classmethod
    def reset(cls) -> None:
        """Stops any running dispatcher and replaces the configuration with a fresh, empty one.

        Mainly for tests and reconfiguration between runs.
        """
        if cls.configuration is not None:
            cls.configuration.stop_dispatcher()
        cls.configuration = Configuration()


def _reinit_after_fork() -> None:
    try:
        HireFire.configuration._reinit_after_fork()
    except Exception:
        pass


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reinit_after_fork)
