def safe_log(logger: object, level: str, message: str) -> None:
    # A logger assigned to config.logger can raise (a custom object, or a closed stream).
    # Routing every library log through here keeps a raising logger from escaping a
    # dispatcher/worker guard and killing the loop, or aborting boot from configure().
    try:
        method = getattr(logger, level, None)
        if callable(method):
            method(message)
    except Exception:
        pass
