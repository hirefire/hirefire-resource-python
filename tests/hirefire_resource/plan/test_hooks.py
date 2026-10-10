from hirefire_resource.plan import hooks


def test_default_plan_hooks_empty():
    assert hooks.plan_options("jql", {"skip_retries": True}) == {}
    assert hooks.plan_connection_options() == {}


def test_default_supports_plan_strategy():
    assert hooks.supports_plan_strategy("jql")
    assert hooks.supports_plan_strategy("jqs")
    assert not hooks.supports_plan_strategy("cpu")


def test_default_queues_required_is_false():
    assert hooks.queues_required() is False


def test_hooks_default_sample_wave_methods_are_noops():
    assert hooks.before_sample_job_queues() is None
    assert hooks.after_sample_job_queues("anything") is None
    assert hooks.reinit_after_fork() is None
    assert hooks.attach() is None
    assert hooks.release() is None


def test_extract_allowlists_and_coerces():
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


def test_coerce_plan_value():
    assert hooks.coerce_plan_value("boolean", True) is True
    assert hooks.coerce_plan_value("boolean", False) is False
    assert hooks.coerce_plan_value("boolean", "true") is None
    assert hooks.coerce_plan_value("non_negative_integer", 3) == 3
    assert hooks.coerce_plan_value("non_negative_integer", -1) is None
    assert hooks.coerce_plan_value("non_negative_integer", "42") == 42
    assert hooks.coerce_plan_value("non_negative_integer", "4.2") is None
    assert hooks.coerce_plan_value("non_negative_integer", 4.2) is None
    assert hooks.coerce_plan_value("non_negative_integer", True) is None
    assert hooks.coerce_plan_value("non_negative_integer", "-1") is None
    assert hooks.coerce_plan_value("non_negative_integer", None) is None


def test_a_whole_number_with_more_digits_than_python_converts_is_not_an_option_value():
    assert hooks.coerce_plan_value("non_negative_integer", "9" * 5000) is None


def test_a_value_of_a_type_the_schema_does_not_know_is_not_an_option_value():
    assert hooks.coerce_plan_value("string", "default") is None


def test_extract_drops_invalid_and_non_dict():
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


def test_extract_reads_the_options_after_one_the_schema_does_not_know():
    schema = {"jqs": {"skip_working": "boolean"}}

    out = hooks.extract_plan_options(
        "jqs", {"unknown": 1, "skip_working": True}, schema
    )

    assert out == {"skip_working": True}


def test_zero_is_a_non_negative_integer():
    assert hooks.coerce_plan_value("non_negative_integer", 0) == 0
    assert hooks.coerce_plan_value("non_negative_integer", "0") == 0
