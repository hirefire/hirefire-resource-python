import logging
import os
import sys
import threading

from hirefire_resource import identity
from hirefire_resource.buffer import Buffer
from hirefire_resource.cpu import CPU
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.web import Web
from hirefire_resource.worker import Worker
from hirefire_resource.workers import Workers

_UNSET = object()


class MissingSamplerError(Exception):
    pass


class UnexpectedSamplerError(Exception):
    pass


class UnknownCollectorError(Exception):
    pass


class DuplicateDynoError(Exception):
    pass


class Configuration:
    SERVICE_COLLECTORS = {"http": "http", "cpu": "cpu"}
    DYNO_COLLECTORS = {"cpu": "cpu"}

    def __init__(self):
        self.web = None
        self.workers = Workers()
        self.cpu = []
        self.logger = self._init_logger()
        self.log_queue_metrics = False
        self._names = []
        self._buffer = None
        self._dispatcher = None
        self._token = None
        self._resolved_identity = _UNSET
        self._mutex = threading.Lock()

    @property
    def token(self):
        return self._token or os.environ.get("HIREFIRE_TOKEN")

    @token.setter
    def token(self, value):
        self._token = value

    def dyno(self, name, proc=None, *, tracking=None):
        """Declares a service.

        Exactly like :meth:`service`, plus the convention that a process named
        "web" implies ``tracking="http"``.

        Resolution: ``tracking="cpu"`` tracks CPU, a sampler tracks job metrics, and the
        name "web" (case-insensitive) tracks http on its own. ``"cpu"`` is the only
        ``tracking`` value ``dyno`` accepts. For an http process under a non-"web"
        name, use ``service(name, tracking="http")``.

        Args:
            name (str): The process name. Must be non-empty.
            proc (callable, optional): A sampler returning the current job-queue
                metric (a non-negative, finite number).
            tracking (str, optional): ``"cpu"``, or omit.

        Raises:
            MissingSamplerError: A non-"web" name given with neither ``tracking="cpu"`` nor a sampler.
            UnexpectedSamplerError: A sampler given alongside ``tracking="cpu"``.
            UnknownCollectorError: ``tracking`` given anything other than ``"cpu"``.
            DuplicateDynoError: The name was already declared, or a second http process was declared.

        Examples:
            >>> config.dyno("web")  # "web" implies http
            >>> config.dyno("worker", lambda: job_queue_size("default"))
            >>> config.dyno("encoder", tracking="cpu")
        """
        name = self._coerce_name(name)

        if tracking is not None:
            collector = self.DYNO_COLLECTORS.get(str(tracking))
            if collector is None:
                raise UnknownCollectorError(
                    f"Unknown value {tracking!r} for config.dyno({name!r}, tracking=...). "
                    "config.dyno only tracks 'cpu'. Pass a sampler callable for job "
                    "metrics, or use config.service to track 'http' explicitly."
                )
        elif proc is not None:
            collector = "job"
        elif name.lower() == "web":
            collector = "http"
        else:
            raise MissingSamplerError(
                f"config.dyno({name!r}) could not be resolved: it needs a sampler callable "
                "(job metrics) or tracking='cpu'. Only the \"web\" name implies http on its "
                f"own. Use config.service({name!r}, tracking='http') for an http process "
                "under another name."
            )

        self._register(name, collector, proc)

    def service(self, name, proc=None, *, tracking=None):
        """Declares what a process tracks.

        The name is a label with no implicit meaning, so what to track is always
        explicit. Pass exactly one of ``tracking`` or a sampler callable:

        - ``tracking="http"``: web request queue-time metrics, sampled from this
          process's own HTTP traffic by the framework middleware (at most one http
          process per app process).
        - a sampler callable returning the current value: job queue metrics,
          typically via a queue macro (e.g. ``job_queue_latency``).
        - ``tracking="cpu"``: this process's CPU utilization.

        :meth:`dyno` is this method plus the convention that the name "web"
        implies ``"http"``.

        Args:
            name (str): The process name. Must be non-empty.
            proc (callable, optional): A sampler returning the current job-queue
                metric (a non-negative, finite number). Omit when passing ``tracking``.
            tracking (str, optional): ``"http"`` or ``"cpu"``. Omit when passing a sampler.

        Raises:
            MissingSamplerError: Neither ``tracking`` nor a sampler was given.
            UnexpectedSamplerError: A sampler given alongside ``tracking="http"`` or ``"cpu"``.
            UnknownCollectorError: ``tracking`` given an unsupported value.
            DuplicateDynoError: The name was already declared, or a second http process was declared.

        Examples:
            >>> config.service("web", tracking="http")
            >>> config.service("worker", lambda: job_queue_size("default"))
            >>> config.service("encoder", tracking="cpu")
        """
        name = self._coerce_name(name)

        if tracking is not None:
            collector = self.SERVICE_COLLECTORS.get(str(tracking))
            if collector is None:
                raise UnknownCollectorError(
                    f"Unknown value {tracking!r} for config.service({name!r}, tracking=...). "
                    "Expected tracking='http' or 'cpu', or a sampler callable for job metrics."
                )
        elif proc is not None:
            collector = "job"
        else:
            raise MissingSamplerError(
                f"config.service({name!r}) could not be resolved: pass tracking='http', "
                "'cpu', or a sampler callable for job metrics."
            )

        self._register(name, collector, proc)

    @property
    def buffer(self):
        if self._buffer is None:
            with self._mutex:
                if self._buffer is None:
                    self._buffer = Buffer()
        return self._buffer

    @property
    def dispatcher(self):
        if self._dispatcher is None:
            with self._mutex:
                if self._dispatcher is None:
                    self._dispatcher = Dispatcher(
                        web=self.web,
                        workers=self.workers,
                        cpu=self.active_cpu_collectors(),
                        web_liveness=self.web_liveness(),
                    )
        return self._dispatcher

    def stop_dispatcher(self):
        if self._dispatcher is not None:
            self._dispatcher.stop()

    def active_cpu_collectors(self):
        if not self.cpu:
            return []

        resolved = self.resolved_identity()

        if resolved is None:
            self.logger.error(
                "[HireFire] CPU metrics are configured but this process's identity "
                "could not be resolved, so the CPU collector is disabled. Set the "
                "HIREFIRE_SERVICE_NAME environment variable to this process's dyno name."
            )
            return []

        return [
            collector
            for collector in self.cpu
            if collector.name.lower() == resolved.lower()
        ]

    def web_liveness(self):
        if not self.web:
            return True

        resolved = self.resolved_identity()
        return resolved is None or resolved.lower() == self.web.name.lower()

    def resolved_identity(self):
        if self._resolved_identity is not _UNSET:
            return self._resolved_identity

        if identity.heroku_conflict():
            self.logger.warning(
                f"[HireFire] HIREFIRE_SERVICE_NAME ({identity.explicit()}) does not match "
                f"the Heroku DYNO prefix ({identity.heroku_dyno()}). Heroku config vars are "
                "app-wide, so this makes every dyno identify as the same name. Set it inline "
                "per process in the Procfile, or unset it to use automatic detection."
            )

        self._resolved_identity = identity.resolve()
        return self._resolved_identity

    def _coerce_name(self, name):
        name = "" if name is None else str(name)

        if name == "":
            raise ValueError(
                "config.dyno and config.service require a dyno name as their first "
                f"argument (got {name!r})."
            )

        return name

    def _register(self, name, collector, proc):
        if any(existing.lower() == name.lower() for existing in self._names):
            raise DuplicateDynoError(
                f"Duplicate declaration for {name!r}. "
                "Each dyno name maps to exactly one collector."
            )
        self._names.append(name)

        if collector == "http":
            self._reject_sampler(name, proc)
            if self.web:
                raise DuplicateDynoError(
                    f"{name!r} conflicts with the earlier http declaration for "
                    f"{self.web.name!r}. Request metrics are collected from this process's "
                    "own http traffic, so only one http collector can be declared, under "
                    "any name."
                )
            self.web = Web(name)
        elif collector == "job":
            if proc is None:
                raise MissingSamplerError(f"Missing sampler for {name!r}")
            self.workers.append(Worker(name, proc))
        elif collector == "cpu":
            self._reject_sampler(name, proc)
            self.cpu.append(CPU(name))

    def _reject_sampler(self, name, proc):
        if proc is None:
            return

        raise UnexpectedSamplerError(
            f"{name!r} does not take a sampler "
            "(its values are collected automatically)."
        )

    def _init_logger(self):
        logger = logging.getLogger("hirefire_resource")
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            logger.propagate = False

        return logger
