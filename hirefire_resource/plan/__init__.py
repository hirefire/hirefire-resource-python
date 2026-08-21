import importlib
import math
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from hirefire_resource.log import safe_log

ADAPTER_MODULES = {
    "celery": "hirefire_resource.macro.celery",
    "rq": "hirefire_resource.macro.rq",
    "dramatiq": "hirefire_resource.macro.dramatiq",
}

LIBRARY_CHECKS = {
    "celery": "celery",
    "rq": "rq",
    "dramatiq": "dramatiq",
}

STRATEGIES = {
    "jql": "job_queue_latency",
    "jqs": "job_queue_size",
}

MAX_QUEUES = 64
MAX_QUEUE_NAME_BYTES = 128


def any_allowlisted_job_queue_library_loaded() -> bool:
    return any(name in sys.modules for name in LIBRARY_CHECKS.values())


def known_adapter(adapter: object) -> bool:
    return str(adapter) in ADAPTER_MODULES


def library_loaded(adapter: object) -> bool:
    key = LIBRARY_CHECKS.get(str(adapter))
    if key is None:
        return False
    return key in sys.modules


def executable(adapter: object) -> bool:
    return known_adapter(adapter) and library_loaded(adapter)


def known_strategy(strategy: object) -> bool:
    return str(strategy) in STRATEGIES


def supports_strategy(adapter: object, strategy: object) -> bool:
    if not known_strategy(strategy):
        return False
    macro = _load_macro(adapter)
    if macro is None:
        return False
    supports = getattr(macro, "supports_plan_strategy", None)
    if supports is None:
        return known_strategy(strategy)
    return bool(supports(strategy))


@contextmanager
def around_job_queue_sample() -> Iterator[None]:
    tokens: dict[str, object] = {}
    for name in ADAPTER_MODULES:
        try:
            macro = _load_macro(name)
            if macro is None:
                continue
            before = getattr(macro, "before_sample_job_queues", None)
            tokens[name] = before() if callable(before) else None
        except Exception as error:
            _log(
                "error",
                f"[HireFire] before_sample_job_queues for {name!r} raised "
                f"{type(error).__name__}: {error}",
            )

    try:
        yield
    finally:
        for name, token in tokens.items():
            try:
                macro = _load_macro(name)
                if macro is None:
                    continue
                after = getattr(macro, "after_sample_job_queues", None)
                if callable(after):
                    after(token)
            except Exception as error:
                _log(
                    "error",
                    f"[HireFire] after_sample_job_queues for {name!r} raised "
                    f"{type(error).__name__}: {error}",
                )


def reinit_macros_after_fork() -> None:
    for name in ADAPTER_MODULES:
        try:
            macro = _load_macro(name)
            if macro is None:
                continue
            reinit = getattr(macro, "reinit_after_fork", None)
            if callable(reinit):
                reinit()
        except Exception as error:
            _log(
                "error",
                f"[HireFire] reinit_after_fork for {name!r} raised "
                f"{type(error).__name__}: {error}",
            )


def execute(entry: dict[str, Any]) -> None:
    adapter = str(entry.get("adapter", "")).strip()
    strategy = str(entry.get("strategy", "")).strip()
    name = str(entry.get("name", "")).strip()
    method_name = STRATEGIES.get(strategy)

    if method_name is None:
        _log(
            "error",
            f"[HireFire] Unknown plan strategy {strategy!r} for {name!r}. Entry skipped.",
        )
        return

    if not known_adapter(adapter):
        _log(
            "error",
            f"[HireFire] Unknown plan adapter {adapter!r} for {name!r}. Entry skipped.",
        )
        return

    macro = _load_macro(adapter)
    if macro is None:
        _log(
            "error",
            f"[HireFire] Plan adapter {adapter!r} could not be loaded for "
            f"{name!r}. Entry skipped.",
        )
        return

    supports = getattr(macro, "supports_plan_strategy", None)
    if supports is not None and not supports(strategy):
        _log(
            "error",
            f"[HireFire] Plan adapter {adapter!r} does not support strategy "
            f"{strategy!r} for {name!r}. Entry skipped.",
        )
        return

    queues = normalize_queues(entry.get("queues"), name=name)
    if queues is None:
        return

    if adapter in ("celery", "dramatiq") and queues == []:
        _log(
            "error",
            f"[HireFire] Plan queue list for {name!r} had no valid names. Entry skipped.",
        )
        return

    try:
        plan_options: dict[str, Any] = getattr(
            macro, "plan_options", lambda _s, _o: {}
        )(strategy, entry.get("options"))
        connection_options: dict[str, Any] = getattr(
            macro, "plan_connection_options", lambda: {}
        )()
        options = {**plan_options, **connection_options}
        if not _sample_job_strategy(
            macro, name, strategy, method_name, queues, options
        ):
            return
        if hasattr(macro, "job_queue_working"):
            _sample_working(macro, name, queues, options)
    except Exception as error:
        _log(
            "error",
            f"[HireFire] Plan sampler for {name!r} raised "
            f"{type(error).__name__}: {error}",
        )


def _sample_job_strategy(
    macro: Any,
    name: str,
    strategy: str,
    method_name: str,
    queues: list[str],
    options: dict[str, Any],
) -> bool:
    method = getattr(macro, method_name)
    value = method(*queues, **options)

    if not _valid_sample(value):
        _log(
            "error",
            f"[HireFire] Plan sampler for {name!r} returned "
            f"{_format_sample_value(value)}, expected a non-negative number. "
            "Sample dropped.",
        )
        return False

    _record_sample(name, strategy, value)
    return True


def _sample_working(
    macro: Any,
    name: str,
    queues: list[str],
    options: dict[str, Any],
) -> None:
    try:
        method = getattr(macro, "job_queue_working")
        wrk = method(*queues, **options)
        if not _valid_sample(wrk):
            _log(
                "error",
                f"[HireFire] Plan working sampler for {name!r} returned "
                f"{_format_sample_value(wrk)}, expected a non-negative number. "
                "wrk sample dropped.",
            )
            return
        _record_sample(name, "wrk", wrk)
    except Exception as error:
        _log(
            "error",
            f"[HireFire] Plan working sampler for {name!r} raised "
            f"{type(error).__name__}: {error}",
        )


def _record_sample(name: str, strategy: str, value: int | float) -> None:
    from hirefire_resource.hirefire import HireFire

    HireFire.configuration.buffer.sample(name, strategy, _coerce_sample(value))


def normalize_queues(queues: object, name: str) -> list[str] | None:
    if queues is None:
        return []

    if not isinstance(queues, list):
        _log(
            "error",
            f"[HireFire] Plan queues for {name!r} must be an array. Entry skipped.",
        )
        return None

    result: list[str] = []
    for queue in queues:
        qname = "" if queue is None else str(queue).strip()
        if not qname or len(qname.encode("utf-8")) > MAX_QUEUE_NAME_BYTES:
            continue
        result.append(qname)

    if not result and queues:
        _log(
            "error",
            f"[HireFire] Plan queue list for {name!r} had no valid names. Entry skipped.",
        )
        return None

    if len(result) > MAX_QUEUES:
        _log(
            "error",
            f"[HireFire] Plan queue list truncated to {MAX_QUEUES} names.",
        )
        result = result[:MAX_QUEUES]

    return result


def _load_macro(adapter: object) -> Any | None:
    module_name = ADAPTER_MODULES.get(str(adapter))
    if module_name is None:
        return None
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def _valid_sample(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _coerce_sample(value: int | float) -> int | float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return float(value)


def _format_sample_value(value: object) -> str:
    try:
        text = type(value).__name__
        preview = str(value)
        encoded = preview.encode("utf-8")
        if len(encoded) > 64:
            preview = encoded[:64].decode("utf-8", "replace") + "…"
        return f"{text}({preview!r})"
    except Exception:
        return type(value).__name__


def _log(level: str, message: str) -> None:
    from hirefire_resource.hirefire import HireFire

    safe_log(HireFire.configuration.logger, level, message)
