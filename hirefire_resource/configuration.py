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
    # The public `tracking` keyword selects one of three internal collectors. The
    # only value that changes the collector is "cpu"; the http and job feeds are
    # each shared by their family (the server derives rqt/rpm from one http feed,
    # and the user's sampler picks the jql/jqs macro over one job feed), so a
    # single "http" value covers the whole HTTP family.
    #
    # service is platform-neutral: the name implies nothing, so http must be named
    # explicitly (tracking="http") alongside "cpu". dyno is the Heroku convenience:
    # the only value it ever takes is "cpu", because the Procfile "web" name implies
    # http on its own (handled in dyno(), not here).
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

    # Legacy / Heroku front door, backwards-compatible with the 1.x implicit forms.
    # The only thing it ever tracks explicitly is "cpu"; the Heroku Procfile
    # convention (the "web" name implies http) is baked in. dyno is exactly
    # service() plus that web => http convenience.
    #
    #   dyno("web")                  # http  (1.x form: name "web" implies it)
    #   dyno("worker", sampler)      # job   (1.x form: the sampler implies it)
    #   dyno("web", tracking="cpu")  # cpu on the web process
    #   dyno("clock", tracking="cpu") # cpu on a non-web process
    #
    # The sampler is the second positional argument (a callable), so the 1.x
    # dyno("worker", callable) form keeps working; tracking is keyword-only.
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

    # Universal / platform-neutral front door. The name carries no meaning, so http
    # must be tracked explicitly with tracking="http"; the sampler still implies job.
    #
    #   service("web", tracking="http")  # http  (any http process name)
    #   service("worker", sampler)       # job   (the sampler implies it)
    #   service("clock", tracking="cpu") # cpu
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

    # Both memoizations are synchronized: the middleware touches them from
    # concurrent request threads, and an unsynchronized check could build (and
    # start) two dispatchers, leaving one running but unreachable.
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

    # CPU is intrinsic to a process's own dyno, so a collector only runs where the
    # process identity matches its declared name. Hard gate: unresolved identity
    # disables CPU with a loud log line rather than raising — a metrics library
    # must not crash the host app.
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

    # Whether this process may synthesize liveness claims (heartbeats/backfill)
    # under the http collector's name. Real request samples self-gate — only the
    # HTTP-serving process receives requests — but without this gate any process
    # running the shared initializer would claim "web alive, zero traffic" seconds
    # while the actual web dynos are down. Soft gate: an unresolved identity still
    # allows the claims, since http must keep working without a resolver.
    def web_liveness(self):
        if not self.web:
            return True

        resolved = self.resolved_identity()
        return resolved is None or resolved.lower() == self.web.name.lower()

    # Memoized so the dispatcher's gates share one resolution and the Heroku
    # app-wide config var footgun is warned about once.
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

    # Coerce the name to a string (so non-string names are interchangeable) and
    # reject an empty result. Shared by both front doors, so the message names both.
    def _coerce_name(self, name):
        name = "" if name is None else str(name)

        if name == "":
            raise ValueError(
                "config.dyno and config.service require a dyno name as their first "
                f"argument (got {name!r})."
            )

        return name

    # Shared back end for both front doors: the duplicate-name guard (spanning dyno
    # and service via the single _names registry) and collector registration. Each
    # front door has already resolved the collector kind and validated its own
    # keyword rules; the per-collector sampler rules (a job needs one, http/cpu
    # reject one) and the one-http-per-process guard live here so they hold
    # identically no matter which front door was used.
    def _register(self, name, collector, proc):
        # Case-insensitive, matching the identity gates: two names differing only
        # in case would both match one process identity and emit under two names.
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

        return logger
