from hirefire_resource.macro import dramatiq as dramatiq_macro


def test_plan_connection_options_empty_without_env(monkeypatch):
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_URL", raising=False)
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_NAMESPACE", raising=False)
    assert dramatiq_macro.plan_connection_options() == {}


def test_plan_connection_options_prefer_hirefire_dramatiq_url(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "redis://hf/0")
    assert dramatiq_macro.plan_connection_options() == {"broker_url": "redis://hf/0"}


def test_plan_connection_options_namespace(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "appns")
    assert dramatiq_macro.plan_connection_options() == {"namespace": "appns"}


def test_plan_connection_options_url_and_namespace(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "amqp://hf")
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "appns")
    assert dramatiq_macro.plan_connection_options() == {
        "broker_url": "amqp://hf",
        "namespace": "appns",
    }


def test_plan_connection_options_ignore_blank(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "   ")
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "  ")
    assert dramatiq_macro.plan_connection_options() == {}


def test_plan_connection_options_blank_url_keeps_namespace(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "")
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "kept")
    assert dramatiq_macro.plan_connection_options() == {"namespace": "kept"}


def test_plan_options_empty_for_v1():
    assert dramatiq_macro.plan_options("jqs", {"skip_working": True}) == {}
    assert dramatiq_macro.plan_options("jql", None) == {}


def test_supports_plan_strategy():
    # Not size-only: both jql and jqs.
    assert dramatiq_macro.supports_plan_strategy("jql")
    assert dramatiq_macro.supports_plan_strategy("jqs")
    assert not dramatiq_macro.supports_plan_strategy("cpu")
    assert not dramatiq_macro.supports_plan_strategy("rpm")
    assert not dramatiq_macro.supports_plan_strategy("jql ")
    assert not dramatiq_macro.supports_plan_strategy(None)
