import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from hirefire_resource.log import format_error, safe_log

ADAPTERS = {
    "celery": "hirefire_resource.macro.celery",
    "rq": "hirefire_resource.macro.rq",
    "dramatiq": "hirefire_resource.macro.dramatiq",
}

MAX_QUEUES = 64
MAX_QUEUE_NAME_BYTES = 128

_import_errors: dict[str, Exception] = {}


def any_library_loaded() -> bool:
    return any(adapter in sys.modules for adapter in ADAPTERS)


def load_macro(adapter: str) -> Any:
    if adapter not in sys.modules:
        return None
    if adapter not in _import_errors:
        try:
            return importlib.import_module(ADAPTERS[adapter])
        except Exception as error:
            _import_errors[adapter] = error
    return _import_errors[adapter]


def attach_macros(logger: object) -> None:
    _each_macro(logger, "attach", lambda _name, macro: macro.attach())


@contextmanager
def around_job_queue_sample(logger: object) -> Iterator[None]:
    tokens: dict[str, object] = {}

    def before(name: str, macro: Any) -> None:
        tokens[name] = macro.before_sample_job_queues()

    def after(name: str, macro: Any) -> None:
        if name in tokens:
            macro.after_sample_job_queues(tokens[name])

    _each_macro(logger, "before_sample_job_queues", before)
    try:
        yield
    finally:
        _each_macro(logger, "after_sample_job_queues", after)


def reinit_macros_after_fork(logger: object) -> None:
    _each_macro(
        logger, "reinit_after_fork", lambda _name, macro: macro.reinit_after_fork()
    )


def release_macros(logger: object) -> None:
    _each_macro(logger, "release", lambda _name, macro: macro.release())


def _each_macro(logger: object, hook: str, call: Callable[[str, Any], None]) -> None:
    for name in ADAPTERS:
        try:
            macro = load_macro(name)
            if macro is not None and not isinstance(macro, Exception):
                call(name, macro)
        except Exception as error:
            safe_log(
                logger,
                "error",
                f"[HireFire] {hook} for {name!r} raised {format_error(error)}",
            )
