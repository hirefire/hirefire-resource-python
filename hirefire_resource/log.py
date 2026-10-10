import os
import re

_USERINFO = re.compile(r"(://)([^/@\s]+)@")


def format_error(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}"
    return _USERINFO.sub(r"\1***@", text)


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
