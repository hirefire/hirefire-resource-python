from hirefire_resource.plan import hooks


def test_default_plan_options_empty():
    assert hooks.plan_options("jql", {"skip_retries": True}) == {}


def test_default_connection_options_empty():
    assert hooks.plan_connection_options() == {}


def test_supports_known_strategies():
    assert hooks.supports_plan_strategy("jql")
    assert hooks.supports_plan_strategy("jqs")
    assert not hooks.supports_plan_strategy("cpu")


def test_default_queues_required_is_false():
    assert hooks.queues_required() is False


def test_sample_wave_defaults_are_noops():
    assert hooks.before_sample_job_queues() is None
    assert hooks.after_sample_job_queues("anything") is None
    assert hooks.reinit_after_fork() is None


def test_extract_plan_options_schema():
    schema = {
        "jql": {"flag": "boolean", "n": "non_negative_integer"},
        "jqs": {"flag": "boolean"},
    }
    out = hooks.extract_plan_options(
        "jql",
        {"flag": True, "n": "10", "unknown": 1, "bad": 1.5},
        schema,
    )
    assert out == {"flag": True, "n": 10}


def test_coerce_plan_value_strict():
    assert hooks.coerce_plan_value("boolean", True) is True
    assert hooks.coerce_plan_value("boolean", False) is False
    assert hooks.coerce_plan_value("boolean", "true") is None
    assert hooks.coerce_plan_value("non_negative_integer", 3) == 3
    assert hooks.coerce_plan_value("non_negative_integer", -1) is None
    assert hooks.coerce_plan_value("non_negative_integer", "42") == 42
    assert hooks.coerce_plan_value("non_negative_integer", "4.2") is None
    assert hooks.coerce_plan_value("non_negative_integer", 4.2) is None
    assert hooks.coerce_plan_value("non_negative_integer", True) is None


def test_extract_drops_invalid_and_non_hash():
    schema = {
        "jqs": {
            "skip_working": "boolean",
            "max_scheduled": "non_negative_integer",
            "server": "boolean",
        }
    }
    assert hooks.extract_plan_options("jqs", None, schema) == {}
    assert hooks.extract_plan_options("jqs", "nope", schema) == {}
    assert hooks.extract_plan_options("unknown", {"server": True}, schema) == {}

    out = hooks.extract_plan_options(
        "jqs",
        {
            "skip_working": "true",
            "max_scheduled": -1,
            "server": True,
        },
        schema,
    )
    assert out == {"server": True}
