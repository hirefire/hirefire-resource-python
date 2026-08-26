"""Uniform plan hooks for every queue macro. Plan always calls these. Adapters
override when they accept lease options or need connection kwargs.

For lease JSON ``options``, prefer a strategy → field → type schema and
:func:`extract_plan_options` so filtering/coercion stays in one place.
"""

import re
from typing import Any

_INT_STRING = re.compile(r"\A[+-]?\d+\Z")


def plan_options(strategy: object, options: object) -> dict[str, Any]:
    """Filter/coerce plan JSON ``options`` for ``strategy`` (``"jql"`` / ``"jqs"``).

    Args:
        strategy: Plan strategy name.
        options: Raw lease ``options`` (usually a dict).

    Returns:
        Keyword args safe to pass to ``job_queue_latency`` / ``job_queue_size``.
    """
    return {}


def plan_connection_options() -> dict[str, Any]:
    """Connection-related kwargs from the environment (for example a broker URL).

    Returns:
        Keyword args for the sampler.
    """
    return {}


def supports_plan_strategy(strategy: object) -> bool:
    """Whether this adapter can sample ``strategy`` (``"jql"`` / ``"jqs"``).

    Defaults to true for known strategies.
    """
    from hirefire_resource import plan

    return plan.known_strategy(strategy)


def queues_required() -> bool:
    """Whether an empty plan queue list is invalid (named queues required).

    Enumerating adapters override this to false (empty = all queues).
    """
    return False


def before_sample_job_queues() -> object | None:
    """Open process-local state for one dispatcher job-queue sample wave.

    Default is a no-op. Adapters with sample-scoped caches override and may
    return an opaque token for :func:`after_sample_job_queues`.

    Called for every allowlisted macro on each job-queue sample wave, whether or
    not that adapter appears in the current lease plan (legacy dyno blocks may
    still invoke the macro).
    """
    return None


def after_sample_job_queues(token: object = None) -> None:
    """Close process-local sample-wave state from :func:`before_sample_job_queues`.

    Default is a no-op. Called from ``finally`` even when a sampler raises.
    """
    return None


def reinit_after_fork() -> None:
    """Reset process-local macro state after fork or abandoned inherited state.

    Default is a no-op. Called next to buffer reinit on the same dispatcher sites.
    """
    return None


def extract_plan_options(
    strategy: object,
    options: object,
    schema: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Slice and coerce lease ``options`` using a strategy-keyed schema.

    ``schema`` maps strategy string to field name string to type name
    (``boolean``, ``non_negative_integer``). Unknown strategies, non-dict
    options, unknown fields, and failed coercions are dropped.
    """
    if not isinstance(options, dict):
        return {}

    fields = schema.get(str(strategy))
    if fields is None:
        return {}

    out: dict[str, Any] = {}
    for key, value in options.items():
        key_str = str(key)
        type_name = fields.get(key_str)
        if type_name is None:
            continue
        coerced = coerce_plan_value(type_name, value)
        if coerced is not None:
            out[key_str] = coerced
    return out


def coerce_plan_value(type_name: str, value: object) -> object | None:
    """Coerce a single plan option value.

    Returns ``None`` when the value is not acceptable for ``type_name`` (caller
    drops the key).
    """
    if type_name == "boolean":
        if value is True:
            return True
        if value is False:
            return False
        return None

    if type_name == "non_negative_integer":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            if not _INT_STRING.match(value):
                return None
            try:
                parsed = int(value, 10)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None
        return None

    return None
