from hirefire_resource.strategy import JQS


def supports_plan_strategy(strategy: object) -> bool:
    return str(strategy) == JQS
