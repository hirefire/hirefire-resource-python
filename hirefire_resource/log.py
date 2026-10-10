import logging
import os
import re

_USERINFO = re.compile(r"(://)[^?\s]*@")


def redact(text: str) -> str:
    return _USERINFO.sub(r"\1***@", text)


def format_error(error: BaseException) -> str:
    return _on_one_line(redact(f"{type(error).__name__}: {error}"))


def _on_one_line(text: str) -> str:
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode()
        for char in text
    )


def verbose() -> bool:
    value = os.environ.get("HIREFIRE_VERBOSE", "")
    return bool(value) and value.lower() not in ("0", "false", "no")


def safe_log(logger: object, level: str, message: str) -> None:
    try:
        if isinstance(logger, logging.Logger) and not logger.hasHandlers():
            _print(logger, level, message)
            return
        method = getattr(logger, level, None)
        if callable(method):
            method(message)
    except Exception:
        pass


def _print(logger: logging.Logger, level: str, message: str) -> None:
    threshold = logger.level or logging.INFO
    if not logger.disabled and getattr(logging, level.upper()) >= threshold:
        print(message, flush=True)
