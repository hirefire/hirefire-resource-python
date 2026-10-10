import os
import re

_USERINFO = re.compile(r"(://)([^/@\s]+)@")


def redact(text: str) -> str:
    return _USERINFO.sub(r"\1***@", text)


def format_error(error: BaseException) -> str:
    return redact(f"{type(error).__name__}: {error}")


def verbose() -> bool:
    value = os.environ.get("HIREFIRE_VERBOSE", "")
    return bool(value) and value.lower() not in ("0", "false", "no")


def safe_log(logger: object, level: str, message: str) -> None:
    try:
        method = getattr(logger, level, None)
        if callable(method):
            method(message)
    except Exception:
        pass
