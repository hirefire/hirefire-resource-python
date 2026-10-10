import importlib
import logging
import sys
from unittest.mock import patch

import pytest

from hirefire_resource import HireFire, plan
from tests.helpers import fake_macro

HOSTS = ("celery", "rq", "dramatiq")


def logger():
    return HireFire.configuration.logger


def messages(caplog):
    return [record.getMessage() for record in caplog.records]


def test_lists_one_macro_module_per_adapter():
    assert plan.ADAPTERS == {
        "celery": "hirefire_resource.macro.celery",
        "rq": "hirefire_resource.macro.rq",
        "dramatiq": "hirefire_resource.macro.dramatiq",
    }


def test_limits_the_queues_of_an_entry():
    assert plan.MAX_QUEUES == 64
    assert plan.MAX_QUEUE_NAME_BYTES == 128


@pytest.mark.parametrize("library", HOSTS)
def test_a_library_counts_as_loaded_once_its_module_is_imported(library):
    without = {key: value for key, value in sys.modules.items() if key not in HOSTS}
    with patch.dict(sys.modules, without, clear=True):
        assert not plan.any_library_loaded()
        sys.modules[library] = object()
        assert plan.any_library_loaded()


def test_a_module_outside_the_adapter_list_does_not_count_as_a_loaded_library():
    without = {key: value for key, value in sys.modules.items() if key not in HOSTS}
    with patch.dict(sys.modules, without, clear=True):
        sys.modules["huey"] = object()
        assert not plan.any_library_loaded()


def test_importing_the_plan_does_not_import_a_job_library():
    before = set(sys.modules)

    importlib.reload(plan)

    assert {name for name in HOSTS if name in sys.modules} <= before


def test_nothing_imports_a_library_that_the_host_has_not_loaded():
    macros = {f"hirefire_resource.macro.{name}" for name in HOSTS}
    removed = {
        key: sys.modules.pop(key)
        for key in list(sys.modules)
        if key.split(".")[0] in HOSTS or key in macros
    }
    try:
        for name in HOSTS:
            assert plan.load_macro(name) is None
        with plan.around_job_queue_sample(logger()):
            pass
        plan.attach_macros(logger())
        plan.reinit_macros_after_fork(logger())
        plan.release_macros(logger())
        for name in HOSTS:
            assert name not in sys.modules
            assert f"hirefire_resource.macro.{name}" not in sys.modules
    finally:
        sys.modules.update(removed)


def test_a_macro_that_fails_to_import_is_returned_as_its_error_and_not_imported_again(
    monkeypatch,
):
    monkeypatch.setattr(plan, "_import_errors", {})
    attempts = []

    def failing(name):
        attempts.append(name)
        raise ImportError("No module named 'dateutil'")

    with (
        patch.dict(sys.modules, {"celery": object()}),
        patch.object(importlib, "import_module", side_effect=failing),
    ):
        first = plan.load_macro("celery")
        second = plan.load_macro("celery")

    assert isinstance(first, ImportError)
    assert str(first) == "No module named 'dateutil'"
    assert second is first
    assert attempts == ["hirefire_resource.macro.celery"]


def test_a_macro_that_raises_another_error_at_import_is_kept_as_its_error(monkeypatch):
    monkeypatch.setattr(plan, "_import_errors", {})
    attempts = []

    def failing(name):
        attempts.append(name)
        raise AttributeError("module 'celery' has no attribute 'signals'")

    with (
        patch.dict(sys.modules, {"celery": object()}),
        patch.object(importlib, "import_module", side_effect=failing),
    ):
        first = plan.load_macro("celery")
        plan.attach_macros(logger())
        second = plan.load_macro("celery")

    assert isinstance(first, AttributeError)
    assert second is first
    assert attempts == ["hirefire_resource.macro.celery"]


def test_attaching_calls_the_attach_hook_of_every_loaded_macro(load_macros, caplog):
    attached = []

    def boom():
        raise RuntimeError("no broker")

    load_macros(
        celery=fake_macro(attach=lambda: attached.append("celery")),
        rq=fake_macro(attach=boom),
        dramatiq=fake_macro(attach=lambda: attached.append("dramatiq")),
    )

    plan.attach_macros(logger())

    assert attached == ["celery", "dramatiq"]
    assert messages(caplog) == [
        "[HireFire] attach for 'rq' raised RuntimeError: no broker"
    ]


def test_a_sample_round_runs_between_the_begin_and_the_end_hook_of_every_macro(
    load_macros,
):
    calls = []

    def macro(name):
        return fake_macro(
            before_sample_job_queues=lambda: calls.append(f"before {name}") or name,
            after_sample_job_queues=lambda token: calls.append(f"after {token}"),
        )

    load_macros(celery=macro("celery"), rq=macro("rq"), dramatiq=macro("dramatiq"))

    with plan.around_job_queue_sample(logger()):
        calls.append("round")

    assert calls == [
        "before celery",
        "before rq",
        "before dramatiq",
        "round",
        "after celery",
        "after rq",
        "after dramatiq",
    ]


def test_the_end_hook_runs_when_the_round_raises(load_macros):
    ended = []
    load_macros(rq=fake_macro(after_sample_job_queues=ended.append))

    with pytest.raises(RuntimeError):
        with plan.around_job_queue_sample(logger()):
            raise RuntimeError("round failed")

    assert ended == [None]


def test_a_begin_hook_that_raises_is_logged_and_its_end_hook_is_skipped(
    load_macros, caplog
):
    calls = []

    def boom():
        raise RuntimeError("redis://user:secret@host/0 is down")

    load_macros(
        celery=fake_macro(
            before_sample_job_queues=boom,
            after_sample_job_queues=lambda token: calls.append("after celery"),
        ),
        rq=fake_macro(
            before_sample_job_queues=lambda: calls.append("before rq"),
            after_sample_job_queues=lambda token: calls.append("after rq"),
        ),
    )

    with plan.around_job_queue_sample(logger()):
        calls.append("round")

    assert calls == ["before rq", "round", "after rq"]
    assert messages(caplog) == [
        "[HireFire] before_sample_job_queues for 'celery' raised "
        "RuntimeError: redis://***@host/0 is down"
    ]


def test_an_end_hook_that_raises_is_logged_and_the_others_still_run(
    load_macros, caplog
):
    calls = []

    def boom(token):
        raise RuntimeError("cache gone")

    load_macros(
        celery=fake_macro(after_sample_job_queues=boom),
        rq=fake_macro(after_sample_job_queues=lambda token: calls.append("after rq")),
    )

    with plan.around_job_queue_sample(logger()):
        pass

    assert calls == ["after rq"]
    assert messages(caplog) == [
        "[HireFire] after_sample_job_queues for 'celery' raised RuntimeError: cache gone"
    ]


def test_a_macro_that_is_not_loaded_or_failed_to_import_gets_no_hook(
    load_macros, caplog
):
    caplog.set_level(logging.DEBUG)
    load_macros(celery=ImportError("broken"), rq=None)
    ran = []

    with plan.around_job_queue_sample(logger()):
        ran.append(True)
    plan.reinit_macros_after_fork(logger())
    plan.release_macros(logger())

    assert ran == [True]
    assert messages(caplog) == []


@pytest.mark.parametrize(
    ("fan_out", "hook"),
    [
        (plan.reinit_macros_after_fork, "reinit_after_fork"),
        (plan.release_macros, "release"),
    ],
)
def test_a_hook_is_fanned_out_to_every_macro_and_one_that_raises_is_logged(
    load_macros, caplog, fan_out, hook
):
    called = []

    def boom():
        raise RuntimeError("no connection")

    load_macros(
        celery=fake_macro(**{hook: boom}),
        rq=fake_macro(**{hook: lambda: called.append("rq")}),
        dramatiq=fake_macro(**{hook: lambda: called.append("dramatiq")}),
    )

    fan_out(logger())

    assert called == ["rq", "dramatiq"]
    assert messages(caplog) == [
        f"[HireFire] {hook} for 'celery' raised RuntimeError: no connection"
    ]


def test_the_default_hooks_of_a_macro_do_nothing():
    macro = fake_macro()

    assert macro.plan_options("jqs", {"skip_working": True}) == {}
    assert macro.plan_connection_options() == {}
    assert macro.supports_plan_strategy("jql") is True
    assert macro.supports_plan_strategy("jqs") is True
    assert macro.supports_plan_strategy("rqt") is False
    assert macro.queues_required() is False
    assert macro.before_sample_job_queues() is None
    assert macro.after_sample_job_queues("token") is None
    assert macro.reinit_after_fork() is None
    assert macro.release() is None
