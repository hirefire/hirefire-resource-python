import logging
import os
import sys
import threading

from hirefire_resource import identity
from hirefire_resource._types import Sampler
from hirefire_resource.buffer import Buffer
from hirefire_resource.cpu import CPU
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.errors import DuplicateDynoError, MissingSamplerError
from hirefire_resource.log import safe_log
from hirefire_resource.web import Web
from hirefire_resource.worker import Worker
from hirefire_resource.workers import Workers

__all__ = [
    "Configuration",
    "DuplicateDynoError",
    "MissingSamplerError",
    "MAX_NAME_BYTES",
]

MAX_NAME_BYTES = 128


class Configuration:
    """Holds process-wide settings (token, logger) and optional local declarations via :meth:`dyno`.

    Always-on sources (request queue time on the HTTP middleware path, and CPU when process
    identity resolves) do not require an explicit :meth:`dyno` declaration. Local job-queue
    sampler callables remain the escape hatch for custom probes.

    Attributes:
        http: Always ``None``. Kept for readers that still check ``configuration.http``.
            Request queue time uses always-on sources under :meth:`http_name` (process identity).
        job_queues: Local job-queue sources declared via sampler callables on :meth:`dyno`.
        logger: Logger used for HireFire diagnostic messages. Defaults to a stdout logger.
            Set to ``None`` (or a logger missing the log methods) to silence diagnostics.
        log_queue_metrics: When true, the HTTP middleware prints
            ``[hirefire:router] queue=…ms`` for each sample.
    """

    def __init__(self) -> None:
        self.http: Web | None = None
        self.job_queues = Workers()
        self.logger = self._init_logger()
        self.log_queue_metrics = False
        self._sources_by_name: dict[str, list[str]] = {}
        self._buffer: Buffer | None = None
        self._dispatcher: Dispatcher | None = None
        self._token: str | None = None
        self._mutex = threading.Lock()
        self._always_on_cpu: CPU | None = None
        self._always_on_http: Web | None = None
        self._http_active = False
        self._heroku_conflict_warned = False
        self._identity_name_too_long_warned = False
        self._rqt_unresolved_warned = False
        self._cpu_unresolved_warned = False
        self._bare_web_dyno_warned = False

    @property
    def web(self) -> Web | None:
        """Alias for :attr:`http` (temporary compatibility until middleware cutover)."""
        return self.http

    @property
    def workers(self) -> Workers:
        """Alias for :attr:`job_queues`."""
        return self.job_queues

    @property
    def token(self) -> str | None:
        """The HireFire API token.

        Returns the value assigned in code when it is not ``None``, else the
        ``HIREFIRE_TOKEN`` environment variable, else ``None``. An empty string (in
        code or from the env) is treated as absent (``None``), so it neither enables
        reporting nor is sent on the wire. Assigning ``None`` clears the in-code
        value so the environment variable is consulted again. Assigning an empty
        string forces the token off even when ``HIREFIRE_TOKEN`` is set. A non-empty
        token present when :meth:`HireFire.configure` or :meth:`HireFire.boot` runs
        starts the dispatcher and enables reporting.
        """
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
        """Declares a process by dyno name (Heroku Procfile-shaped).

        A sampler callable registers a local job-queue source. Prefer zero-config for
        request queue time and CPU, and lease plan adapters in the HireFire UI for managed
        job queues.

        Bare ``dyno("web")`` (no sampler, name ``"web"`` case-insensitive) is accepted for
        1.x backwards compatibility but does nothing: RQT is armed only by platform web
        role and middleware traffic. A once-per-process warning explains that the line can
        be removed. ``dyno("web", sampler)`` still registers a job-queue sampler under
        ``"web"``.

        Args:
            name: The process name. Must be non-empty.
            sampler: A sampler returning the current job-queue metric (a non-negative,
                finite number).

        Raises:
            ValueError: The name is empty or exceeds 128 UTF-8 bytes.
            MissingSamplerError: A name other than ``"web"`` given without a sampler.
            DuplicateDynoError: The name was already declared for the same source kind.
        """
        name = self._coerce_name(name)

        if sampler is not None:
            self._register(name, "job_queue", sampler)
            return

        if name.lower() == "web":
            self._warn_bare_web_dyno_once()
            return

        raise MissingSamplerError(
            f'config.dyno("{name}") could not be resolved: it needs a sampler '
            "(job-queue metrics). Request queue time is always-on via platform web role "
            "or middleware traffic; CPU is always-on when process identity resolves. "
            'Bare config.dyno("web") is a no-op and can be removed.'
        )

    @property
    def buffer(self) -> Buffer:
        """In-memory metric buffer that accumulates samples between dispatcher flushes."""
        if self._buffer is None:
            with self._mutex:
                if self._buffer is None:
                    self._buffer = Buffer()
        return self._buffer

    @property
    def dispatcher(self) -> Dispatcher:
        """Periodic reporter that samples job queues and CPU and flushes buffered metrics."""
        if self._dispatcher is None:
            with self._mutex:
                if self._dispatcher is None:
                    self._dispatcher = Dispatcher()
        return self._dispatcher

    def stop_dispatcher(self, flush: bool = True) -> None:
        """Stops the dispatcher if one was started.

        Args:
            flush: Forwarded to :meth:`Dispatcher.stop`.
        """
        if self._dispatcher is not None:
            self._dispatcher.stop(flush=flush)

    def http_name(self) -> str | None:
        """Process name used for request-queue-time metrics.

        Resolved process identity only. No invented default (e.g. not ``"web"``).
        """
        return self.soft_identity()

    def mark_http_active(self) -> None:
        """Marks this process as serving HTTP (middleware has sampled)."""
        self._http_active = True

    def rqt_enabled(self) -> bool:
        """Whether this process should emit the ``rqt`` wire metric."""
        return bool(self._http_active or identity.platform_http_role())

    def http_source(self) -> Web | None:
        """HTTP source used for sampling, creating always-on when name is known."""
        name = self.http_name()
        if name is None:
            if self.token and (self._http_active or identity.platform_http_role()):
                self._warn_rqt_unresolved_once()
            return None

        if (
            self._always_on_http is None
            or self._always_on_http.name.lower() != name.lower()
        ):
            self._always_on_http = Web(name)
        return self._always_on_http

    def rqt_liveness(self) -> bool:
        """Whether ``rqt`` liveness claims may be synthesized for this process."""
        if not self.rqt_enabled():
            return False

        resolved = self.soft_identity()
        name = self.http_name()
        if resolved is None or name is None:
            return False

        return resolved.lower() == name.lower()

    def active_cpu_sources(self) -> list[CPU]:
        """Always-on CPU source for this process when identity resolves."""
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
        """Drop process-local always-on source instances after a fork."""
        self._always_on_cpu = None
        self._always_on_http = None

    def prefork_web_handoff(self) -> bool:
        """Whether this process participates in prefork web master → worker handoff."""
        return self.rqt_enabled()

    def soft_identity(self) -> str | None:
        """Resolved process identity with soft length gate (re-resolves every call)."""
        self._warn_heroku_conflict_once()
        name = identity.resolve()
        if name is None:
            return None
        if len(name.encode("utf-8")) <= MAX_NAME_BYTES:
            return name
        self._warn_identity_name_too_long_once(name)
        return None

    def _reinit_after_fork(self) -> None:
        self._mutex = threading.Lock()
        if self._buffer is not None:
            self._buffer.reinit_after_fork()
        if self._dispatcher is not None:
            self._dispatcher._reinit_after_fork()
        self.reset_after_fork()

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
            assert sampler is not None
            self.job_queues.append(Worker(self._canonical_name(name), sampler))

        self._sources_by_name[key] = kinds + [source]

    def _canonical_name(self, name: str) -> str:
        existing_key = next(
            (key for key in self._sources_by_name if key.lower() == name.lower()),
            None,
        )
        if existing_key is None:
            return name

        for worker in self.job_queues:
            if worker.name.lower() == name.lower():
                return worker.name
        return name

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
            '[HireFire] config.dyno("web") without a sampler is no longer '
            "necessary. Request queue time is armed by platform web identity "
            "(for example DYNO type web or RENDER_SERVICE_TYPE=web) and by HTTP "
            "middleware traffic. You can remove this line.",
        )

    def _warn_rqt_unresolved_once(self) -> None:
        if self._rqt_unresolved_warned:
            return
        self._rqt_unresolved_warned = True
        safe_log(
            self.logger,
            "warning",
            "[HireFire] Request queue time samples dropped: process identity "
            "is unresolved. Set HIREFIRE_SERVICE_NAME, or rely on DYNO / "
            "RENDER_SERVICE_NAME where available.",
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
            "Set HIREFIRE_SERVICE_NAME, or rely on DYNO / RENDER_SERVICE_NAME where "
            "available.",
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
