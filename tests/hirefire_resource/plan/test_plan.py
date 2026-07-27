import sys
from unittest.mock import patch

from hirefire_resource import HireFire, plan


def test_known_adapters_and_strategies():
    assert plan.known_adapter("celery")
    assert plan.known_adapter("rq")
    assert not plan.known_adapter("sidekiq")
    assert plan.known_strategy("jql")
    assert plan.known_strategy("jqs")
    assert not plan.known_strategy("cpu")


def test_library_loaded_uses_sys_modules_only():
    assert not plan.library_loaded("celery") or "celery" in sys.modules
    # Installing is not enough: only already-imported modules count.
    with patch.dict(sys.modules, {"celery": object()}, clear=False):
        assert plan.library_loaded("celery")
        assert plan.any_allowlisted_job_queue_library_loaded()


def test_normalize_queues_rules():
    assert plan.normalize_queues(None, name="worker") == []
    assert plan.normalize_queues(["a", "b"], name="worker") == ["a", "b"]
    assert plan.normalize_queues("nope", name="worker") is None
    assert plan.normalize_queues(["", "  "], name="worker") is None
    # JSON null list elements must not become the literal queue name "None".
    assert plan.normalize_queues([None, "ok", None], name="worker") == ["ok"]
    assert plan.normalize_queues([None, None], name="worker") is None
    long_name = "q" * 200
    assert plan.normalize_queues([long_name, "ok"], name="worker") == ["ok"]


def test_execute_samples_buffer_with_mock_macro():
    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_latency(*queues, **options):
            return 12.5

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jql",
                "queues": ["default"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert list(data["worker"]["jql"].values())[0] == 12.5


def test_execute_rejects_bool_sample(caplog):
    import logging

    caplog.set_level(logging.ERROR)

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_size(*queues, **options):
            return True

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
            }
        )

    assert HireFire.configuration.buffer.flush() == {}
    assert "expected a non-negative number" in caplog.text


def test_plan_import_does_not_load_celery_rq():
    # Importing plan must not pull optional backends into sys.modules.
    mods = set(sys.modules)
    import importlib

    importlib.reload(plan)
    assert "celery" not in sys.modules or "celery" in mods
    assert "rq" not in sys.modules or "rq" in mods


def test_execute_skips_unsupported_strategy_without_calling_macro(caplog):
    import logging

    caplog.set_level(logging.ERROR)
    called = {"n": 0}

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return strategy != "jql"

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_size(*queues, **options):
            called["n"] += 1
            return 1

        @staticmethod
        def job_queue_latency(*queues, **options):
            called["n"] += 1
            return 1

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jql",
                "queues": ["default"],
            }
        )

    assert called["n"] == 0
    assert HireFire.configuration.buffer.flush() == {}
    assert "does not support" in caplog.text


def test_execute_rescues_macro_errors_and_logs(caplog):
    import logging

    caplog.set_level(logging.ERROR)

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_latency(*queues, **options):
            raise RuntimeError("broker down")

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jql",
                "queues": ["default"],
            }
        )

    assert HireFire.configuration.buffer.flush() == {}
    assert "broker down" in caplog.text


def test_execute_drops_invalid_samples(caplog):
    import logging

    caplog.set_level(logging.ERROR)

    for bad in (float("nan"), float("inf"), -1, None, "x"):

        class Macro:
            @staticmethod
            def supports_plan_strategy(strategy):
                return True

            @staticmethod
            def plan_options(strategy, options):
                return {}

            @staticmethod
            def plan_connection_options():
                return {}

            @staticmethod
            def job_queue_size(*queues, **options):
                return bad

        with patch.object(plan, "_load_macro", return_value=Macro):
            plan.execute(
                {
                    "name": "worker",
                    "adapter": "rq",
                    "strategy": "jqs",
                    "queues": ["default"],
                }
            )
        assert HireFire.configuration.buffer.flush() == {}


def test_execute_merges_adapter_plan_connection_options():
    captured = {}

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {"url": "redis://plan"}

        @staticmethod
        def job_queue_latency(*queues, **options):
            captured.update(options)
            return 1.0

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jql",
                "queues": ["default"],
            }
        )
    assert captured.get("url") == "redis://plan"


def test_execute_merges_adapter_plan_options():
    captured = {}

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {"skip_working": True}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_size(*queues, **options):
            captured.update(options)
            return 3

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
                "options": {"skip_working": True, "not_allowed": True},
            }
        )
    assert captured == {"skip_working": True}


def test_supports_strategy_rejects_unknown_strategy_with_known_adapter():
    with patch.dict(sys.modules, {"celery": object()}, clear=False):
        assert not plan.supports_strategy("celery", "rpm")
        assert not plan.supports_strategy("celery", "unknown")
