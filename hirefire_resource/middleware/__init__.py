import time

from hirefire_resource import HireFire


# Runs in the host's request path, so it must never raise into the request:
# failures are logged and swallowed.
def process_request_queue_time(request_start):
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
        HireFire.configuration.logger.error(
            f"[HireFire] Failed to process request queue time: {error}"
        )


def log_request_queue_time(request_queue_time):
    print(f"[hirefire:router] queue={request_queue_time}ms")


# X-Request-Start's unit varies by router (epoch s / ms / µs), so infer it from
# magnitude. Implausible or unparseable values yield None.
def calculate_request_queue_time(request_start):
    value = _parse_timestamp(request_start)
    if value is None or value < 1e9:
        return None

    if value < 1e11:
        milliseconds = value * 1000  # epoch seconds
    elif value < 1e14:
        milliseconds = value  # epoch milliseconds
    else:
        milliseconds = value / 1000  # epoch microseconds

    return max(int(time.time() * 1000) - int(milliseconds), 0)


def _parse_timestamp(request_start):
    text = str(request_start)
    if text.startswith("t="):
        text = text[2:]

    try:
        return float(text)
    except ValueError:
        return None
