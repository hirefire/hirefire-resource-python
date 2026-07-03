def safe_log(logger: object, level: str, message: str) -> None:
    try:
        method = getattr(logger, level, None)
        if callable(method):
            method(message)
    except Exception:
        pass
