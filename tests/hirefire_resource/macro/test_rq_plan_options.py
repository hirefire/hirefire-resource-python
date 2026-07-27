import pytest

redis = pytest.importorskip("redis")

from hirefire_resource.macro import rq as rq_macro  # noqa: E402


def test_plan_connection_options_empty_without_env():
    assert rq_macro.plan_connection_options() == {}


def test_plan_connection_options_prefer_hirefire_rq_url(monkeypatch):
    monkeypatch.setenv("HIREFIRE_RQ_URL", "redis://hf/1")
    assert rq_macro.plan_connection_options() == {"redis_url": "redis://hf/1"}


def test_plan_connection_options_ignore_blank(monkeypatch):
    monkeypatch.setenv("HIREFIRE_RQ_URL", "   ")
    assert rq_macro.plan_connection_options() == {}


def test_supports_plan_strategy():
    assert rq_macro.supports_plan_strategy("jql")
    assert rq_macro.supports_plan_strategy("jqs")
