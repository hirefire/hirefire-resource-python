import sys
from unittest.mock import patch

from hirefire_resource import HireFire, plan


def test_known_adapters_and_strategies():
    assert plan.known_adapter("celery")
    assert plan.known_adapter("rq")
    assert plan.known_adapter("dramatiq")
    assert not plan.known_adapter("sidekiq")
    assert plan.known_strategy("jql")
    assert plan.known_strategy("jqs")
    assert not plan.known_strategy("cpu")


def test_library_loaded_uses_sys_modules_only():
    assert not plan.library_loaded("celery") or "celery" in sys.modules
    with patch.dict(sys.modules, {"celery": object()}, clear=False):
        assert plan.library_loaded("celery")
        assert plan.any_allowlisted_job_queue_library_loaded()


def test_load_macro_does_not_import_unloaded_host_library():
    hosts = ("celery", "rq", "dramatiq")
    adapter_modules = {f"hirefire_resource.macro.{name}" for name in hosts}
    removed = {}
    keys = [
        k
        for k in list(sys.modules)
        if k in hosts
        or any(k.startswith(f"{name}.") for name in hosts)
        or k in adapter_modules
    ]
    for key in keys:
        removed[key] = sys.modules.pop(key)
    try:
        for name in hosts:
            assert plan._load_macro(name) is None
        with plan.around_job_queue_sample():
            pass
        plan.reinit_macros_after_fork()
        for name in hosts:
            assert name not in sys.modules
            assert f"hirefire_resource.macro.{name}" not in sys.modules
    finally:
        sys.modules.update(removed)


def test_library_loaded_detects_dramatiq_from_sys_modules():
    with patch.dict(sys.modules, {"dramatiq": object()}, clear=False):
        assert plan.library_loaded("dramatiq")
        assert plan.executable("dramatiq")
    without = {k: v for k, v in sys.modules.items() if k != "dramatiq"}
    with patch.dict(sys.modules, without, clear=True):
        assert plan.known_adapter("dramatiq")
        assert not plan.library_loaded("dramatiq")


def test_normalize_queues_rules():
    assert plan.normalize_queues(None, name="worker") == []
    assert plan.normalize_queues(["a", "b"], name="worker") == ["a", "b"]
    assert plan.normalize_queues("nope", name="worker") is None
    assert plan.normalize_queues(["", "  "], name="worker") is None
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


def test_execute_live_gate_drops_a_sample_that_returns_after_stop():
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
            return 11

        @staticmethod
        def job_queue_working(*queues, **options):
            return 3

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
            },
            lambda: False,
        )

    assert HireFire.configuration.buffer.flush() == {}


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


def test_plan_import_does_not_load_celery_rq_dramatiq():
    mods = set(sys.modules)
    import importlib

    importlib.reload(plan)
    assert "celery" not in sys.modules or "celery" in mods
    assert "rq" not in sys.modules or "rq" in mods
    assert "dramatiq" not in sys.modules or "dramatiq" in mods


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


def test_execute_skips_dramatiq_empty_queues(caplog):
    import logging

    caplog.set_level(logging.ERROR)
    called = {"n": 0}

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
            called["n"] += 1
            return 1

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "dramatiq",
                "strategy": "jqs",
                "queues": [],
            }
        )

    assert called["n"] == 0
    assert HireFire.configuration.buffer.flush() == {}
    assert "no valid names" in caplog.text


def test_execute_skips_celery_empty_queues(caplog):
    import logging

    caplog.set_level(logging.ERROR)
    called = {"n": 0}

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
            called["n"] += 1
            return 1

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jqs",
                "queues": [],
            }
        )

    assert called["n"] == 0
    assert HireFire.configuration.buffer.flush() == {}
    assert "no valid names" in caplog.text


def test_execute_dramatiq_passes_plan_connection_options():
    captured = {}

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return strategy in ("jql", "jqs")

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {
                "broker_url": "redis://from-hirefire/0",
                "namespace": "appns",
            }

        @staticmethod
        def job_queue_size(*queues, **options):
            captured["queues"] = queues
            captured["options"] = options
            return 4

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "dramatiq",
                "strategy": "jqs",
                "queues": ["default", "mailer"],
            }
        )

    assert captured["queues"] == ("default", "mailer")
    assert captured["options"] == {
        "broker_url": "redis://from-hirefire/0",
        "namespace": "appns",
    }
    data = HireFire.configuration.buffer.flush()
    assert list(data["worker"]["jqs"].values())[0] == 4


def test_supports_strategy_dramatiq_jql_and_jqs():
    with patch.dict(sys.modules, {"dramatiq": object()}, clear=False):
        assert plan.supports_strategy("dramatiq", "jql")
        assert plan.supports_strategy("dramatiq", "jqs")
        assert not plan.supports_strategy("dramatiq", "cpu")


def test_execute_dramatiq_jql_passes_connection_options():
    captured = {}

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return strategy in ("jql", "jqs")

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {"broker_url": "redis://hf-jql/0", "namespace": "ns"}

        @staticmethod
        def job_queue_latency(*queues, **options):
            captured["queues"] = queues
            captured["options"] = options
            return 12.5

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "dramatiq",
                "strategy": "jql",
                "queues": ["default"],
            }
        )

    assert captured["queues"] == ("default",)
    assert captured["options"] == {
        "broker_url": "redis://hf-jql/0",
        "namespace": "ns",
    }
    data = HireFire.configuration.buffer.flush()
    assert list(data["worker"]["jql"].values())[0] == 12.5


def test_around_job_queue_sample_calls_before_and_after_on_every_adapter():
    events: list[object] = []

    class MacroA:
        @staticmethod
        def before_sample_job_queues():
            events.append(("before", "a"))
            return "token_a"

        @staticmethod
        def after_sample_job_queues(token):
            events.append(("after", "a", token))

    class MacroB:
        @staticmethod
        def before_sample_job_queues():
            events.append(("before", "b"))
            return "token_b"

        @staticmethod
        def after_sample_job_queues(token):
            events.append(("after", "b", token))

    macros = {"a": MacroA, "b": MacroB}

    def load(name):
        return macros.get(str(name))

    with (
        patch.object(plan, "ADAPTER_MODULES", {"a": "mod.a", "b": "mod.b"}),
        patch.object(plan, "_load_macro", side_effect=load),
    ):
        with plan.around_job_queue_sample():
            events.append("body")

    assert events == [
        ("before", "a"),
        ("before", "b"),
        "body",
        ("after", "a", "token_a"),
        ("after", "b", "token_b"),
    ]


def test_around_job_queue_sample_runs_after_when_body_raises():
    after_tokens: list[object] = []

    class Macro:
        @staticmethod
        def before_sample_job_queues():
            return "wave"

        @staticmethod
        def after_sample_job_queues(token):
            after_tokens.append(token)

    with (
        patch.object(plan, "ADAPTER_MODULES", {"x": "mod.x"}),
        patch.object(plan, "_load_macro", return_value=Macro),
    ):
        try:
            with plan.around_job_queue_sample():
                raise RuntimeError("boom")
        except RuntimeError as error:
            assert str(error) == "boom"

    assert after_tokens == ["wave"]


def test_reinit_macros_after_fork_notifies_every_adapter():
    called: list[str] = []

    class MacroA:
        @staticmethod
        def reinit_after_fork():
            called.append("a")

    class MacroB:
        @staticmethod
        def reinit_after_fork():
            called.append("b")

    macros = {"a": MacroA, "b": MacroB}

    def load(name):
        return macros.get(str(name))

    with (
        patch.object(plan, "ADAPTER_MODULES", {"a": "mod.a", "b": "mod.b"}),
        patch.object(plan, "_load_macro", side_effect=load),
    ):
        plan.reinit_macros_after_fork()

    assert called == ["a", "b"]


def test_around_job_queue_sample_continues_when_before_raises_and_skips_its_after(
    caplog,
):
    import logging

    caplog.set_level(logging.ERROR)
    events: list[object] = []

    class MacroA:
        @staticmethod
        def before_sample_job_queues():
            events.append(("before", "a"))
            raise RuntimeError("before-a")

        @staticmethod
        def after_sample_job_queues(token):
            events.append(("after", "a", token))

    class MacroB:
        @staticmethod
        def before_sample_job_queues():
            events.append(("before", "b"))
            return "token_b"

        @staticmethod
        def after_sample_job_queues(token):
            events.append(("after", "b", token))

    macros = {"a": MacroA, "b": MacroB}

    def load(name):
        return macros.get(str(name))

    with (
        patch.object(plan, "ADAPTER_MODULES", {"a": "mod.a", "b": "mod.b"}),
        patch.object(plan, "_load_macro", side_effect=load),
    ):
        with plan.around_job_queue_sample():
            events.append("body")

    assert events == [
        ("before", "a"),
        ("before", "b"),
        "body",
        ("after", "b", "token_b"),
    ]
    assert "before_sample_job_queues for 'a' raised" in caplog.text
    assert "before-a" in caplog.text


def test_around_job_queue_sample_continues_remaining_afters_when_one_after_raises(
    caplog,
):
    import logging

    caplog.set_level(logging.ERROR)
    events: list[object] = []

    class MacroA:
        @staticmethod
        def before_sample_job_queues():
            return "token_a"

        @staticmethod
        def after_sample_job_queues(token):
            events.append(("after", "a"))
            raise RuntimeError("after-a")

    class MacroB:
        @staticmethod
        def before_sample_job_queues():
            return "token_b"

        @staticmethod
        def after_sample_job_queues(token):
            events.append(("after", "b", token))

    macros = {"a": MacroA, "b": MacroB}

    def load(name):
        return macros.get(str(name))

    with (
        patch.object(plan, "ADAPTER_MODULES", {"a": "mod.a", "b": "mod.b"}),
        patch.object(plan, "_load_macro", side_effect=load),
    ):
        with plan.around_job_queue_sample():
            events.append("body")

    assert events == [
        "body",
        ("after", "a"),
        ("after", "b", "token_b"),
    ]
    assert "after_sample_job_queues for 'a' raised" in caplog.text
    assert "after-a" in caplog.text


def test_reinit_macros_after_fork_continues_when_one_adapter_raises(caplog):
    import logging

    caplog.set_level(logging.ERROR)
    called: list[str] = []

    class MacroA:
        @staticmethod
        def reinit_after_fork():
            called.append("a")
            raise RuntimeError("reinit-a")

    class MacroB:
        @staticmethod
        def reinit_after_fork():
            called.append("b")

    macros = {"a": MacroA, "b": MacroB}

    def load(name):
        return macros.get(str(name))

    with (
        patch.object(plan, "ADAPTER_MODULES", {"a": "mod.a", "b": "mod.b"}),
        patch.object(plan, "_load_macro", side_effect=load),
    ):
        plan.reinit_macros_after_fork()

    assert called == ["a", "b"]
    assert "reinit_after_fork for 'a' raised" in caplog.text
    assert "reinit-a" in caplog.text


def test_around_job_queue_sample_soft_skips_missing_macro_without_logging(caplog):
    import logging

    caplog.set_level(logging.ERROR)
    events: list[object] = []

    class MacroPresent:
        @staticmethod
        def before_sample_job_queues():
            events.append("before-present")
            return "tok"

        @staticmethod
        def after_sample_job_queues(token):
            events.append(("after-present", token))

    def load(name):
        if str(name) == "missing":
            return None
        return MacroPresent

    with (
        patch.object(
            plan, "ADAPTER_MODULES", {"missing": "mod.missing", "present": "mod.p"}
        ),
        patch.object(plan, "_load_macro", side_effect=load),
    ):
        with plan.around_job_queue_sample():
            events.append("body")

    assert events == ["before-present", "body", ("after-present", "tok")]
    assert "before_sample_job_queues" not in caplog.text
    assert "after_sample_job_queues" not in caplog.text


def test_load_macro_import_error_returns_none_without_raising():
    with (
        patch.object(
            plan,
            "ADAPTER_MODULES",
            {"celery": "hirefire_resource.macro.no_such_module"},
        ),
        patch.dict(sys.modules, {"celery": object()}),
    ):
        assert plan._load_macro("celery") is None
        assert plan._load_macro("unknown") is None


def test_allowlisted_macros_reexport_sample_wave_hooks_as_noops():
    import importlib

    from hirefire_resource.plan import hooks

    results: dict[str, str] = {}
    for name, module_name in plan.ADAPTER_MODULES.items():
        try:
            macro = importlib.import_module(module_name)
        except ImportError:
            results[name] = "skip"
            continue
        if name == "dramatiq":
            assert macro.before_sample_job_queues is not hooks.before_sample_job_queues
            token = macro.before_sample_job_queues()
            macro.after_sample_job_queues(token)
            macro.reinit_after_fork()
            results[name] = "ok"
            continue
        assert macro.before_sample_job_queues is hooks.before_sample_job_queues
        assert macro.after_sample_job_queues is hooks.after_sample_job_queues
        assert macro.reinit_after_fork is hooks.reinit_after_fork
        assert macro.before_sample_job_queues() is None
        assert macro.after_sample_job_queues("token") is None
        assert macro.reinit_after_fork() is None
        results[name] = "ok"

    assert results.get("dramatiq") == "ok"
    for name, status in results.items():
        assert status in ("ok", "skip"), name


def test_around_job_queue_sample_calls_after_with_successful_none_token():
    after_tokens: list[object] = []

    class Macro:
        @staticmethod
        def before_sample_job_queues():
            return None

        @staticmethod
        def after_sample_job_queues(token):
            after_tokens.append(token)

    with (
        patch.object(plan, "ADAPTER_MODULES", {"x": "mod.x"}),
        patch.object(plan, "_load_macro", return_value=Macro),
    ):
        with plan.around_job_queue_sample():
            pass

    assert after_tokens == [None]


def test_around_job_queue_sample_with_empty_adapters_still_runs_body():
    events: list[object] = []

    with patch.object(plan, "ADAPTER_MODULES", {}):
        with plan.around_job_queue_sample():
            events.append("body")

    assert events == ["body"]


def test_execute_known_adapter_unloadable_logs_distinct_from_unknown(caplog):
    import logging

    caplog.set_level(logging.ERROR)

    with patch.object(plan, "_load_macro", return_value=None):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jql",
                "queues": ["default"],
            }
        )
        plan.execute(
            {
                "name": "worker",
                "adapter": "nope",
                "strategy": "jql",
                "queues": ["default"],
            }
        )

    assert "could not be loaded" in caplog.text
    assert "Unknown plan adapter" in caplog.text
    assert HireFire.configuration.buffer.flush() == {}


def test_execute_samples_wrk_when_macro_implements_job_queue_working():
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
            return 7

        @staticmethod
        def job_queue_working(*queues, **options):
            assert list(queues) == ["default"]
            return 3

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert list(data["worker"]["jqs"].values())[0] == 7
    assert list(data["worker"]["wrk"].values())[0] == 3


def test_execute_still_samples_wrk_when_job_strategy_sample_invalid():
    working_called = False

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
            return -1

        @staticmethod
        def job_queue_working(*queues, **options):
            nonlocal working_called
            working_called = True
            return 3

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert data.get("worker", {}).get("jqs") is None
    assert list(data["worker"]["wrk"].values())[0] == 3
    assert working_called is True


def test_execute_still_samples_wrk_when_job_strategy_raises(caplog):
    import logging

    caplog.set_level(logging.ERROR)
    working_called = False

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
            raise RuntimeError("jqs boom")

        @staticmethod
        def job_queue_working(*queues, **options):
            nonlocal working_called
            working_called = True
            return 3

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert data.get("worker", {}).get("jqs") is None
    assert list(data["worker"]["wrk"].values())[0] == 3
    assert working_called is True
    assert "Plan sampler for" in caplog.text
    assert "raised" in caplog.text
    assert "jqs boom" in caplog.text


def test_execute_skips_wrk_when_macro_lacks_job_queue_working():
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
            return 5

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jqs",
                "queues": ["default"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert list(data["worker"]["jqs"].values())[0] == 5
    assert "wrk" not in data["worker"]


def test_execute_keeps_jqs_when_job_queue_working_raises(caplog):
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
            return 9

        @staticmethod
        def job_queue_working(*queues, **options):
            raise RuntimeError("wrk boom")

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert list(data["worker"]["jqs"].values())[0] == 9
    assert "wrk" not in data.get("worker", {})
    assert "Plan working sampler" in caplog.text
    assert "wrk boom" in caplog.text


def test_execute_drops_invalid_wrk_keeps_jqs(caplog):
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
            return 4

        @staticmethod
        def job_queue_working(*queues, **options):
            return -2

    with patch.object(plan, "_load_macro", return_value=Macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["default"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert list(data["worker"]["jqs"].values())[0] == 4
    assert "wrk" not in data.get("worker", {})
    assert "wrk sample dropped" in caplog.text
