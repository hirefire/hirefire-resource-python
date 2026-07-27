import re
from typing import Any

_INT_STRING = re.compile(r"\A[+-]?\d+\Z")


def plan_options(strategy: object, options: object) -> dict[str, Any]:
    return {}


def plan_connection_options() -> dict[str, Any]:
    return {}


def supports_plan_strategy(strategy: object) -> bool:
    from hirefire_resource import plan

    return plan.known_strategy(strategy)


def extract_plan_options(
    strategy: object,
    options: object,
    schema: dict[str, dict[str, str]],
) -> dict[str, Any]:
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
