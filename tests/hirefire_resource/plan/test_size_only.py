from hirefire_resource.plan import size_only


def test_supports_jqs_only():
    assert size_only.supports_plan_strategy("jqs")
    assert not size_only.supports_plan_strategy("jql")
    assert not size_only.supports_plan_strategy("cpu")
    assert not size_only.supports_plan_strategy(None)
