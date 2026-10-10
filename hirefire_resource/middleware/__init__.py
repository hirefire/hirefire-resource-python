import math
import re
import time
from collections.abc import Callable

from hirefire_resource import HireFire
from hirefire_resource.identity import presence
from hirefire_resource.log import format_error
from hirefire_resource.utility import round_half_up

REQUEST_QUEUE_TIME_LIMIT = 60_000

_LEADING_NUMBER = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def process_request_queue_time(
    request_start: str | None = None,
    *,
    extract: Callable[[], object | None] | None = None,
) -> None:
    try:
        configuration = HireFire.configuration
        if not configuration.samples_requests():
            return

        if extract is not None:
            request_start = extract()  # type: ignore[assignment]
        request_start = presence(request_start)
        if not request_start:
            return

        request_queue_time = calculate_request_queue_time(request_start)
        if request_queue_time is not None:
            configuration.sample_request_queue_time(request_queue_time)
    except Exception as error:
        _log_once(error)


def _log_once(error: Exception) -> None:
    HireFire.configuration.once.log(
        "error",
        "middleware_error",
        lambda: f"[HireFire] Middleware error: {format_error(error)}",
        key=type(error).__name__,
    )


def calculate_request_queue_time(request_start: str) -> int | None:
    value = _parse_timestamp(request_start)
    if value is None or not math.isfinite(value) or value < 1e9:
        return None

    if value < 1e11:
        milliseconds = value * 1000
    elif value < 1e14:
        milliseconds = value
    elif value < 1e17:
        milliseconds = value / 1000
    else:
        milliseconds = value / 1_000_000

    request_queue_time = max(
        int(time.time() * 1000) - int(round_half_up(milliseconds)), 0
    )
    if request_queue_time <= REQUEST_QUEUE_TIME_LIMIT:
        return request_queue_time
    return None


def _parse_timestamp(request_start: str) -> float | None:
    text = str(request_start)
    if text.startswith("t="):
        text = text[2:]

    match = _LEADING_NUMBER.match(text)
    return float(match.group(1)) if match else None
