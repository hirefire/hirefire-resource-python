import math


def valid_sample(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def coerce_sample(value: object) -> int | float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int):
        return value
    return float(value)


def format_sample_value(value: object) -> str:
    try:
        text = type(value).__name__
        preview = str(value)
        encoded = preview.encode("utf-8")
        if len(encoded) > 64:
            preview = encoded[:64].decode("utf-8", "replace") + "…"
        return f"{text}({preview!r})"
    except Exception:
        return type(value).__name__
