from datetime import datetime

import pytest

celery = pytest.importorskip("celery")

from hirefire_resource.macro import celery as celery_macro  # noqa: E402
from hirefire_resource.macro.celery import run_at_header_signal  # noqa: E402


def test_plan_connection_options_empty_without_env():
    assert celery_macro.plan_connection_options() == {}


def test_run_at_header_signal_preserves_eta():
    headers = {"eta": "2026-08-26T11:00:00+00:00", "task": "test_task"}

    run_at_header_signal(headers=headers)

    assert headers["run_at"] == headers["eta"]
    assert headers["task"] == "test_task"


def test_run_at_header_signal_sets_current_time_without_eta():
    headers = {"task": "test_task"}

    run_at_header_signal(headers=headers)

    parsed = datetime.fromisoformat(headers["run_at"])
    assert parsed.tzinfo is not None
    assert headers["task"] == "test_task"


def test_plan_connection_options_prefer_hirefire_celery_broker_url(monkeypatch):
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "redis://hf/0")
    assert celery_macro.plan_connection_options() == {"broker_url": "redis://hf/0"}


def test_plan_connection_options_ignore_blank(monkeypatch):
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "   ")
    assert celery_macro.plan_connection_options() == {}


def test_supports_plan_strategy():
    assert celery_macro.supports_plan_strategy("jql")
    assert celery_macro.supports_plan_strategy("jqs")
    assert not celery_macro.supports_plan_strategy("cpu")


def test_plan_options_allowlist_skip_working_for_jqs():
    options = celery_macro.plan_options(
        "jqs", {"skip_working": True, "broker_url": "redis://other/0"}
    )

    assert options == {"skip_working": True}


def test_plan_options_keep_a_false_skip_working():
    assert celery_macro.plan_options("jqs", {"skip_working": False}) == {
        "skip_working": False
    }


def test_plan_options_drop_a_non_boolean_skip_working():
    assert celery_macro.plan_options("jqs", {"skip_working": "true"}) == {}
    assert celery_macro.plan_options("jqs", {"skip_working": 1}) == {}
    assert celery_macro.plan_options("jqs", {"skip_working": None}) == {}
    assert celery_macro.plan_options("jqs", None) == {}


def test_plan_options_jql_never_receives_skip_working():
    assert celery_macro.plan_options("jql", {"skip_working": True}) == {}


def test_sample_wave_hooks_default_to_noops():
    from hirefire_resource.plan import hooks

    assert celery_macro.before_sample_job_queues is hooks.before_sample_job_queues
    assert celery_macro.after_sample_job_queues is hooks.after_sample_job_queues
    assert celery_macro.before_sample_job_queues() is None
    assert celery_macro.after_sample_job_queues("token") is None


def test_reinit_after_fork_is_the_macros_own_hook():
    from hirefire_resource.plan import hooks

    assert celery_macro.reinit_after_fork is not hooks.reinit_after_fork
    assert celery_macro.reinit_after_fork() is None


def test_the_macro_takes_the_strategies_and_options_the_server_sends():
    assert celery_macro.supports_plan_strategy("jqs") is True
    assert celery_macro.supports_plan_strategy("jql") is True
    assert celery_macro.queues_required() is True
    assert hasattr(celery_macro, "job_queue_working") is True
    assert celery_macro.plan_options("jqs", {"skip_working": True}) == {
        "skip_working": True
    }
    assert celery_macro.plan_options("jqs", {}) == {}
    assert celery_macro.plan_options("jql", {"skip_working": True}) == {}
