import re
import time

from hirefire_resource import HireFire
from hirefire_resource.log import safe_log

REQUEST_QUEUE_TIME_LIMIT = 60_000

_LEADING_NUMBER = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def process_request_queue_time(request_start: str | None) -> None:
    if not request_start:
        return

    try:
        request_queue_time = calculate_request_queue_time(request_start)
        if request_queue_time is None:
            return

        configuration = HireFire.configuration

        if configuration.web and configuration.token:
            configuration.web.sample(request_queue_time)
            configuration.dispatcher.start()

        if configuration.log_queue_metrics:
            log_request_queue_time(request_queue_time)
    except Exception as error:
        safe_log(
            HireFire.configuration.logger,
            "error",
            f"[HireFire] Middleware error: {error}",
        )


def log_request_queue_time(request_queue_time: int) -> None:
    print(f"[hirefire:router] queue={request_queue_time}ms")


def calculate_request_queue_time(request_start: str) -> int | None:
    value = _parse_timestamp(request_start)
    if value is None or value < 1e9:
        return None

    if value < 1e11:
        milliseconds = value * 1000
    elif value < 1e14:
        milliseconds = value
    elif value < 1e17:
        milliseconds = value / 1000
    else:
        milliseconds = value / 1_000_000

    request_queue_time = max(int(time.time() * 1000) - round(milliseconds), 0)
    if request_queue_time <= REQUEST_QUEUE_TIME_LIMIT:
        return request_queue_time
    return None


def _parse_timestamp(request_start: str) -> float | None:
    text = str(request_start)
    if text.startswith("t="):
        text = text[2:]

    match = _LEADING_NUMBER.match(text)
    if match is None:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None
