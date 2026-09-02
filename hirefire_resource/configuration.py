import logging
import os
import sys
import threading

from hirefire_resource import identity
from hirefire_resource._types import Sampler
from hirefire_resource.buffer import Buffer
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.errors import DuplicateDynoError, MissingSamplerError
from hirefire_resource.log import safe_log
from hirefire_resource.source.cpu import CPU
from hirefire_resource.source.http import HTTP
from hirefire_resource.source.job_queue import JobQueue
from hirefire_resource.source.job_queues import JobQueues

__all__ = [
    "Configuration",
    "DuplicateDynoError",
    "MissingSamplerError",
]

MAX_NAME_BYTES = 128


class Configuration:
    def __init__(self) -> None:
        self.http: HTTP | None = None
        self.job_queues = JobQueues(self)
        self.logger = self._init_logger()
        self._sources_by_name: dict[str, list[str]] = {}
        self._buffer: Buffer | None = None
        self._dispatcher: Dispatcher | None = None
        self._token: str | None = None
        self._mutex = threading.Lock()
        self._always_on_cpu: CPU | None = None
        self._always_on_http: HTTP | None = None
        self._http_active = False
        self._heroku_conflict_warned = False
        self._identity_name_too_long_warned = False
        self._rqt_unresolved_warned = False
        self._cpu_unresolved_warned = False
        self._bare_web_dyno_warned = False

    @property
    def token(self) -> str | None:
        value = (
            self._token if self._token is not None else os.environ.get("HIREFIRE_TOKEN")
        )
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped if stripped else None

    @token.setter
    def token(self, value: str | None) -> None:
        self._token = value

    def dyno(self, name: str, sampler: Sampler | None = None) -> None:
        name = self._coerce_name(name)

        if sampler is not None:
            if not callable(sampler):
                raise TypeError(
                    f"config.dyno({name!r}) sampler must be callable, "
                    f"got {type(sampler).__name__}."
                )
            self._register(name, "job_queue", sampler)
            return

        if name.lower() == "web":
            self._warn_bare_web_dyno_once()
            return

        raise MissingSamplerError(
            f'config.dyno("{name}") could not be resolved: it needs a sampler '
            "(job-queue metrics). Request queue time is always-on via platform web role "
            "or middleware traffic. CPU is always-on when process identity resolves. "
            'Bare config.dyno("web") is a no-op and can be removed.'
        )

    @property
    def buffer(self) -> Buffer:
        if self._buffer is None:
            with self._mutex:
                if self._buffer is None:
                    self._buffer = Buffer()
        return self._buffer

    @property
    def dispatcher(self) -> Dispatcher:
        if self._dispatcher is None:
            with self._mutex:
                if self._dispatcher is None:
                    self._dispatcher = Dispatcher()
        return self._dispatcher

    def stop_dispatcher(self, flush: bool = True) -> None:
        if self._dispatcher is not None:
            self._dispatcher.stop(flush=flush)

    def http_name(self) -> str | None:
        return self.soft_identity()

    def mark_http_active(self) -> None:
        self._http_active = True

    def rqt_enabled(self) -> bool:
        return bool(self._http_active or identity.platform_http_role())

    def http_source(self) -> HTTP | None:
        name = self.http_name()
        if name is None:
            if self.token and (self._http_active or identity.platform_http_role()):
                self._warn_rqt_unresolved_once()
            return None

        if (
            self._always_on_http is None
            or self._always_on_http.name.lower() != name.lower()
        ):
            self._always_on_http = HTTP(name)
        return self._always_on_http

    def rqt_liveness(self) -> bool:
        return self.rqt_enabled() and self.soft_identity() is not None

    def active_cpu_sources(self) -> list[CPU]:
        resolved = self.soft_identity()
        if resolved is None:
            self._warn_cpu_unresolved_once()
            return []

        if (
            self._always_on_cpu is None
            or self._always_on_cpu.name.lower() != resolved.lower()
        ):
            self._always_on_cpu = CPU(resolved)
        return [self._always_on_cpu]

    def reset_after_fork(self) -> None:
        self._always_on_cpu = None
        self._always_on_http = None

    def prefork_web_handoff(self) -> bool:
        return self.rqt_enabled()

    def soft_identity(self) -> str | None:
        self._warn_heroku_conflict_once()
        name = identity.resolve()
        if name is None:
            return None
        if len(name.encode("utf-8")) <= MAX_NAME_BYTES:
            return name
        self._warn_identity_name_too_long_once(name)
        return None

    def _reinit_locks_after_fork(self) -> None:
        self._mutex = threading.Lock()
        if self._buffer is not None:
            self._buffer.reinit_locks_after_fork()
        if self._dispatcher is not None:
            self._dispatcher._reinit_locks_after_fork()

    def _coerce_name(self, name: object | None) -> str:
        coerced = "" if name is None else str(name).strip()

        if not coerced:
            raise ValueError(
                f"config.dyno requires a dyno name as its first argument (got {coerced!r})."
            )

        byte_len = len(coerced.encode("utf-8"))
        if byte_len > MAX_NAME_BYTES:
            raise ValueError(
                f"config.dyno name exceeds {MAX_NAME_BYTES} bytes (got {byte_len})."
            )

        return coerced

    def _register(self, name: str, source: str, sampler: Sampler | None) -> None:
        key = name.lower()
        kinds = list(self._sources_by_name.get(key, []))

        if source in kinds:
            raise DuplicateDynoError(
                f"Duplicate declaration for {name!r}. "
                "Each dyno name maps to at most one source of each kind."
            )

        if source == "job_queue":
            if sampler is None:
                raise MissingSamplerError(
                    f"config.dyno({name!r}) could not be resolved: it needs a sampler."
                )
            self.job_queues.append(JobQueue(name, sampler))

        self._sources_by_name[key] = kinds + [source]

    def _warn_identity_name_too_long_once(self, name: str) -> None:
        if self._identity_name_too_long_warned:
            return
        self._identity_name_too_long_warned = True
        byte_len = len(name.encode("utf-8"))
        safe_log(
            self.logger,
            "error",
            f"[HireFire] Process identity exceeds {MAX_NAME_BYTES} bytes "
            f"({byte_len}). Metrics under this identity are disabled until the "
            "name is shortened.",
        )

    def _warn_bare_web_dyno_once(self) -> None:
        if self._bare_web_dyno_warned:
            return
        self._bare_web_dyno_warned = True
        safe_log(
            self.logger,
            "warning",
            '[HireFire] config.dyno("web") is deprecated. It does nothing. '
            "Request queue time is sampled automatically from HTTP traffic. "
            "You can remove this line. Leaving it does not break anything.",
        )

    def _warn_rqt_unresolved_once(self) -> None:
        if self._rqt_unresolved_warned:
            return
        self._rqt_unresolved_warned = True
        safe_log(
            self.logger,
            "warning",
            "[HireFire] Request queue time samples dropped: process identity "
            "is unresolved. Set HIREFIRE_SERVICE_NAME or DYNO.",
        )

    def _warn_heroku_conflict_once(self) -> None:
        if self._heroku_conflict_warned:
            return
        if not identity.heroku_conflict():
            return
        self._heroku_conflict_warned = True
        safe_log(
            self.logger,
            "warning",
            f"[HireFire] HIREFIRE_SERVICE_NAME ({identity.explicit()}) does not "
            f"match the Heroku DYNO prefix ({identity.heroku_dyno()}). Heroku config "
            "vars are app-wide, so this makes every dyno identify as the same name. "
            "Set it inline per process in the Procfile, or unset it to use automatic "
            "detection.",
        )

    def _warn_cpu_unresolved_once(self) -> None:
        if self._cpu_unresolved_warned:
            return
        self._cpu_unresolved_warned = True
        safe_log(
            self.logger,
            "warning",
            "[HireFire] CPU metrics disabled: process identity is unresolved. "
            "Set HIREFIRE_SERVICE_NAME or DYNO.",
        )

    def _init_logger(self) -> logging.Logger:
        logger = logging.getLogger("hirefire_resource")
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            logger.propagate = False

        return logger
