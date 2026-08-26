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


def test_sample_wave_hooks_default_to_noops():
    from hirefire_resource.plan import hooks

    assert celery_macro.before_sample_job_queues is hooks.before_sample_job_queues
    assert celery_macro.after_sample_job_queues is hooks.after_sample_job_queues
    assert celery_macro.reinit_after_fork is hooks.reinit_after_fork
    assert celery_macro.before_sample_job_queues() is None
    assert celery_macro.after_sample_job_queues("token") is None
    assert celery_macro.reinit_after_fork() is None
