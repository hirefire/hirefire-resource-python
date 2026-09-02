import re

_USERINFO = re.compile(r"(://)([^/@\s]+)@")


def format_error(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}"
    return _USERINFO.sub(r"\1***@", text)


def safe_log(logger: object, level: str, message: str) -> None:
    try:
        method = getattr(logger, level, None)
        if callable(method):
            method(message)
    except Exception:
        pass
