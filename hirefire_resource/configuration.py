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
        name = self._coerce_name(name)

        if tracking is not None:
            collector = self.DYNO_COLLECTORS.get(str(tracking))
            if collector is None:
                raise UnknownCollectorError(
                    f"Unknown value {tracking!r} for config.dyno({name!r}, tracking=...). "
                    "config.dyno only tracks 'cpu'; pass a sampler callable for job "
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
                f"own; use config.service({name!r}, tracking='http') for an http process "
                "under another name."
            )

        self._register(name, collector, proc)

    def service(self, name, proc=None, *, tracking=None):
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

    # Locked double-checked init: concurrent request threads must not build two
    # buffers/dispatchers.
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
        # Case-insensitive: names differing only in case would gate as one identity.
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
            # No propagation: would otherwise double-emit through the host's root logger.
            logger.propagate = False

        return logger
