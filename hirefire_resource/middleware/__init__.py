import math
import re
import time
from collections.abc import Callable

from hirefire_resource import HireFire
from hirefire_resource.log import safe_log

REQUEST_QUEUE_TIME_LIMIT = 60_000

_LEADING_NUMBER = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def present_header(value: object | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None


def process_request_queue_time(
    request_start: str | None = None,
    *,
    extract: Callable[[], object | None] | None = None,
) -> None:
    """Parse a request-start header and record a queue-time sample when configured.

    When a token is present, records a queue-time sample (milliseconds) under the process
    HTTP name and starts the dispatcher. Explicit http registration is optional.
    When ``log_queue_metrics`` is true, also prints a 1.x-compatible
    ``[hirefire:router] queue=<ms>ms`` line to stdout (no token required; Logplex
    QueueTime BC). Failures in this path (including header extract when ``extract``
    is used) are logged and swallowed so the host app is unaffected.
    """
    try:
        if extract is not None:
            request_start = extract()  # type: ignore[assignment]
        request_start = present_header(request_start)
        if not request_start:
            return

        request_queue_time = calculate_request_queue_time(request_start)
        if request_queue_time is None:
            return

        configuration = HireFire.configuration

        if configuration.log_queue_metrics:
            log_request_queue_time(request_queue_time)

        if configuration.token:
            configuration.mark_http_active()
            source = configuration.http_source()
            if source is not None:
                source.sample(request_queue_time)
            configuration.dispatcher.start()
            configuration.dispatcher.ensure_job_queue_loop()
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
