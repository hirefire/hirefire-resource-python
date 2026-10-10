import math
import numbers


def coerce_sample(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, numbers.Number):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


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
