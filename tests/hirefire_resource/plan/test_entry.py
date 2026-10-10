import pytest

from hirefire_resource import plan
from hirefire_resource.plan.entry import Entry
from tests.helpers import fake_macro


def entry(**fields):
    return Entry({"name": "worker", "strategy": "jqs", "adapter": "rq", **fields})


def test_reads_the_name_the_strategy_and_the_adapter_as_text():
    read = Entry({"name": "worker", "strategy": "jql", "adapter": None})

    assert (read.name, read.strategy, read.adapter) == ("worker", "jql", "")
    assert Entry({}).name == ""
    assert Entry({"name": 7, "strategy": 8, "adapter": 9}).key() == ("7", "9", "8")


@pytest.mark.parametrize("adapter", [None, ""])
def test_an_entry_without_an_adapter_is_local_and_never_sampleable(adapter):
    local = Entry({"name": "worker", "strategy": "jql", "adapter": adapter})

    assert local.local()
    assert local.problem is None
    assert not local.sampleable()


def test_a_local_entry_with_an_unknown_strategy_has_a_problem():
    unknown = Entry({"name": "worker", "strategy": "rpm"})

    assert unknown.problem == "unknown_strategy"
    assert unknown.problem_message() == (
        "Unknown plan strategy 'rpm' for 'worker'. Entry skipped."
    )
    assert unknown.key() == ("worker", "", "rpm")


def test_an_adapter_outside_the_list_is_unknown(load_macros):
    load_macros()
    unknown = entry(adapter="sidekiq")

    assert unknown.problem == "unknown_adapter"
    assert not unknown.sampleable()
    assert unknown.problem_message() == (
        "Unknown plan adapter 'sidekiq' for 'worker'. Entry skipped."
    )


def test_an_adapter_whose_library_is_not_loaded_is_unloaded(load_macros):
    load_macros(rq=None)
    unloaded = entry()

    assert unloaded.problem == "unloaded_adapter"
    assert unloaded.problem_message() == (
        "Plan adapter 'rq' for 'worker' is not loaded in this process. Entry skipped."
    )


def test_an_adapter_whose_macro_fails_to_import_names_the_error_once_per_adapter(
    load_macros,
):
    load_macros(rq=ImportError("No module named 'redis'"))
    broken = entry()

    assert broken.problem == "broken_adapter"
    assert not broken.sampleable()
    assert broken.problem_message() == (
        "Plan adapter 'rq' could not be imported "
        "(ImportError: No module named 'redis'). Its entries are skipped."
    )
    assert broken.key() == ("rq",)
    assert entry(name="mailer").key() == ("rq",)


def test_a_strategy_the_adapter_does_not_take_is_unsupported(load_macros):
    load_macros(rq=fake_macro(supports_plan_strategy=lambda strategy: False))
    unsupported = entry()

    assert unsupported.problem == "unsupported_strategy"
    assert unsupported.problem_message() == (
        "Plan adapter 'rq' does not support strategy 'jqs' for 'worker'. Entry skipped."
    )
    assert unsupported.key() == ("worker", "rq", "jqs")


@pytest.mark.parametrize("queues", [None, [], ["", None, "  "], "default"])
def test_an_adapter_that_requires_queues_has_a_problem_without_a_valid_name(
    load_macros, queues
):
    load_macros(rq=fake_macro(queues_required=lambda: True))
    missing = entry(queues=queues)

    assert missing.problem == "queues_required"
    assert missing.problem_message() == (
        "Plan adapter 'rq' for 'worker' requires named queues. Entry skipped."
    )


def test_a_queue_list_that_is_not_a_list_is_a_problem(load_macros):
    load_macros(rq=fake_macro())
    wrong = entry(queues="default")

    assert wrong.problem == "queues_not_a_list"
    assert wrong.problem_message() == (
        "Plan queues for 'worker' must be an array. Entry skipped."
    )


def test_a_queue_list_without_a_valid_name_is_a_problem(load_macros):
    load_macros(rq=fake_macro())
    empty = entry(queues=["", None, "x" * (plan.MAX_QUEUE_NAME_BYTES + 1)])

    assert empty.problem == "no_valid_queues"
    assert empty.problem_message() == (
        "Plan queue list for 'worker' had no valid names. Entry skipped."
    )


@pytest.mark.parametrize("queues", [None, [], ["default"]])
def test_an_entry_without_a_problem_is_sampleable(load_macros, queues):
    load_macros(rq=fake_macro())
    fine = entry(queues=queues)

    assert fine.problem is None
    assert fine.sampleable()
    assert not fine.local()


def test_queue_names_are_stripped_and_invalid_ones_are_left_out(load_macros):
    load_macros(rq=fake_macro())
    longest = "x" * plan.MAX_QUEUE_NAME_BYTES
    read = entry(queues=[" default ", "", None, longest, longest + "x", 7])

    assert read.queues() == ["default", longest, "7"]
    assert not read.truncated()
    assert entry().queues() == []


def test_a_queue_name_is_measured_in_bytes(load_macros):
    load_macros(rq=fake_macro())

    assert entry(queues=["é" * 64]).queues() == ["é" * 64]
    assert entry(queues=["é" * 65]).problem == "no_valid_queues"


def test_a_queue_list_over_the_limit_is_cut_to_its_first_names(load_macros):
    load_macros(rq=fake_macro())
    names = [f"q{index}" for index in range(plan.MAX_QUEUES + 1)]

    assert not entry(queues=names[:-1]).truncated()
    assert entry(queues=names).truncated()
    assert entry(queues=names).queues() == names[:-1]


def test_calls_the_macro_with_the_queues_and_the_options_of_the_entry(load_macros):
    calls = []
    load_macros(
        rq=fake_macro(
            plan_options=lambda strategy, options: (
                {"skip_working": options["skip"]} if options else {}
            ),
            plan_connection_options=lambda: {"redis_url": "redis://sample"},
            job_queue_size=lambda *queues, **options: calls.append((queues, options))
            or 5,
            job_queue_latency=lambda *queues, **options: 9.5,
        )
    )

    assert entry(queues=["a", "b"], options={"skip": True}).call() == 5
    assert entry(strategy="jql").call() == 9.5
    assert calls == [
        (("a", "b"), {"skip_working": True, "redis_url": "redis://sample"})
    ]


def test_a_connection_option_wins_over_a_plan_option_of_the_same_name(load_macros):
    seen = {}
    load_macros(
        rq=fake_macro(
            plan_options=lambda strategy, options: {"redis_url": "from-the-plan"},
            plan_connection_options=lambda: {"redis_url": "from-the-process"},
            job_queue_size=lambda *queues, **options: seen.update(options) or 1,
        )
    )

    entry().call()

    assert seen == {"redis_url": "from-the-process"}


def test_the_working_count_gets_the_queues_and_the_connection_options_only(
    load_macros,
):
    calls = []
    load_macros(
        rq=fake_macro(
            plan_options=lambda strategy, options: {"skip_working": True},
            plan_connection_options=lambda: {"redis_url": "redis://sample"},
            job_queue_working=lambda *queues, **options: calls.append((queues, options))
            or 3,
        )
    )

    assert entry(queues=["a"]).working() == 3
    assert calls == [(("a",), {"redis_url": "redis://sample"})]


def test_the_connection_options_are_read_once_per_entry(load_macros):
    reads = []
    load_macros(
        rq=fake_macro(
            plan_connection_options=lambda: reads.append(1) or {},
            job_queue_size=lambda *queues, **options: 1,
            job_queue_working=lambda *queues, **options: 1,
        )
    )
    sampled = entry(strategy="jql")

    sampled.samples_working()
    sampled.working()

    assert reads == [1]


def test_the_working_count_is_sampled_for_latency_and_for_a_size_that_skips_it(
    load_macros,
):
    skipping = {"skip_working": True}
    load_macros(
        rq=fake_macro(
            plan_options=lambda strategy, options: options or {},
            job_queue_working=lambda *queues, **options: 1,
        )
    )

    assert entry(strategy="jql").samples_working()
    assert entry(strategy="jqs", options=skipping).samples_working()
    assert not entry(strategy="jqs").samples_working()
    assert not entry(strategy="jqs", options={"skip_working": 1}).samples_working()


def test_the_working_count_is_not_sampled_for_a_macro_without_one(load_macros):
    load_macros(rq=fake_macro())

    assert not entry(strategy="jql").samples_working()
