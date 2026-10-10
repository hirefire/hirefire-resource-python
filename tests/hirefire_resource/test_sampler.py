import logging
import threading
from decimal import Decimal
from fractions import Fraction

import pytest

from hirefire_resource import HireFire, plan
from hirefire_resource.configuration import Configuration
from hirefire_resource.errors import SampleIncompleteError, SampleNotReadyError
from hirefire_resource.sampler import Sampler
from tests.helpers import fake_macro

RQ_SIZE = {"name": "worker", "strategy": "jqs", "adapter": "rq", "queues": ["default"]}
LOCAL = {"name": "worker", "strategy": "jql"}


@pytest.fixture
def config():
    return HireFire.configuration


@pytest.fixture
def sampler(config):
    return Sampler(config)


def always():
    return True


def sampled(config):
    return {
        name: {
            strategy: list(series.values()) for strategy, series in strategies.items()
        }
        for name, strategies in config.buffer.flush().items()
    }


def messages(caplog, level=logging.WARNING):
    return [record.getMessage() for record in caplog.records if record.levelno >= level]


def raises(error):
    def sample(*args, **kwargs):
        raise error

    return sample


def test_a_process_with_a_local_sampler_can_sample_any_plan(config, sampler):
    config.dyno("worker", lambda: 1)

    assert sampler.can_sample([])
    assert sampler.can_sample([{"name": "other", "strategy": "jql"}])


def test_a_process_without_a_local_sampler_can_sample_a_plan_with_a_working_adapter(
    sampler, load_macros
):
    load_macros(rq=fake_macro())

    assert sampler.can_sample([{"name": "x", "strategy": "rpm"}, RQ_SIZE])


def test_a_process_without_a_local_sampler_cannot_sample_a_plan_it_has_nothing_for(
    sampler, load_macros
):
    load_macros(rq=None)

    assert not sampler.can_sample([])
    assert not sampler.can_sample([LOCAL])
    assert not sampler.can_sample([RQ_SIZE])
    assert not sampler.can_sample([{**RQ_SIZE, "adapter": "sidekiq"}])


def test_a_local_sampler_is_recorded_under_the_strategy_of_the_plan(config, sampler):
    config.dyno("worker", lambda: 42)
    config.dyno("mailer", lambda: 18)

    sampler.round([LOCAL, {"name": "mailer", "strategy": "jqs"}], always)

    assert sampled(config) == {"worker": {"jql": [42]}, "mailer": {"jqs": [18]}}


def test_a_local_sample_is_reported_under_the_name_of_the_plan_entry(config, sampler):
    config.dyno("Worker", lambda: 4)

    sampler.round([LOCAL], always)

    assert sampled(config) == {"worker": {"jql": [4]}}


def test_the_latest_sample_of_a_second_wins(config, sampler):
    values = iter([5, 9])
    config.dyno("worker", lambda: next(values))

    sampler.round([LOCAL], always)
    sampler.round([LOCAL], always)

    assert sampled(config) == {"worker": {"jql": [9]}}


def test_a_sample_of_zero_is_recorded(config, sampler):
    config.dyno("worker", lambda: 0)

    sampler.round([LOCAL], always)

    assert sampled(config) == {"worker": {"jql": [0]}}


def test_an_async_local_sampler_is_awaited_off_the_main_thread(config, sampler):
    threads = []

    async def sample():
        threads.append(threading.current_thread())
        return 42

    config.dyno("worker", sample)
    worker = threading.Thread(target=lambda: sampler.round([LOCAL], always))
    worker.start()
    worker.join()

    assert sampled(config) == {"worker": {"jql": [42]}}
    assert threads == [worker]


def test_a_local_sampler_that_raises_is_logged_and_the_next_entry_is_sampled(
    config, sampler, caplog
):
    config.dyno("worker", raises(RuntimeError("redis://user:secret@host/0 is down")))
    config.dyno("mailer", lambda: 18)

    sampler.round([LOCAL, {"name": "mailer", "strategy": "jql"}], always)

    assert sampled(config) == {"mailer": {"jql": [18]}}
    assert messages(caplog) == [
        "[HireFire] The sampler for 'worker' raised "
        "RuntimeError: redis://***@host/0 is down"
    ]


def test_an_async_local_sampler_that_raises_is_logged(config, sampler, caplog):
    async def sample():
        raise RuntimeError("Redis down")

    config.dyno("worker", sample)

    sampler.round([LOCAL], always)

    assert sampled(config) == {}
    assert messages(caplog) == [
        "[HireFire] The sampler for 'worker' raised RuntimeError: Redis down"
    ]


def test_a_sampler_that_is_not_ready_is_dropped_without_a_line(config, sampler, caplog):
    caplog.set_level(logging.DEBUG)
    config.dyno("worker", raises(SampleNotReadyError("still counting")))

    sampler.round([LOCAL], always)

    assert sampled(config) == {}
    assert messages(caplog, logging.DEBUG) == []


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (float("nan"), "float('nan')"),
        (float("inf"), "float('inf')"),
        (-1, "int('-1')"),
        (None, "NoneType('None')"),
        ("7", "str('7')"),
        (True, "bool('True')"),
        ([1], "list('[1]')"),
        (Decimal("-1"), "Decimal('-1')"),
        (Decimal("NaN"), "Decimal('NaN')"),
        (Decimal("sNaN"), "Decimal('sNaN')"),
        (Decimal("Infinity"), "Decimal('Infinity')"),
        (Decimal("1e400"), "Decimal('1E+400')"),
        (Fraction(-1, 2), "Fraction('-1/2')"),
        (Fraction(10**400), f"Fraction('1{'0' * 63}…')"),
        (1j, "complex('1j')"),
    ],
)
def test_a_value_that_is_not_a_non_negative_number_is_dropped_and_logged(
    config, sampler, caplog, value, shown
):
    config.dyno("worker", lambda: value)

    sampler.round([LOCAL], always)

    assert sampled(config) == {}
    assert messages(caplog) == [
        f"[HireFire] The sampler for 'worker' returned {shown}, "
        "expected a non-negative number. Sample dropped."
    ]


@pytest.mark.parametrize(
    ("value", "recorded"),
    [
        (Decimal("7.5"), 7.5),
        (Decimal("3"), 3.0),
        (Decimal("0"), 0.0),
        (Fraction(15, 2), 7.5),
        (Fraction(3), 3.0),
    ],
)
def test_a_decimal_or_a_fraction_is_recorded_as_a_float(
    config, sampler, caplog, value, recorded
):
    config.dyno("worker", lambda: value)

    sampler.round([LOCAL], always)

    (sample,) = sampled(config)["worker"]["jql"]
    assert (type(sample), sample) == (float, recorded)
    assert messages(caplog) == []


def test_a_whole_number_too_large_for_a_float_is_recorded_as_it_is(
    config, sampler, caplog
):
    config.dyno("worker", lambda: 10**400)

    sampler.round([LOCAL], always)

    assert sampled(config) == {"worker": {"jql": [10**400]}}
    assert messages(caplog) == []


def test_a_sample_that_returns_after_the_round_ended_is_not_recorded(config, sampler):
    config.dyno("worker", lambda: 9)
    answers = iter([True, False])

    sampler.round([LOCAL], lambda: next(answers))

    assert sampled(config) == {}


def test_a_round_stops_at_the_first_entry_after_it_ended(config, sampler):
    called = []
    config.dyno("worker", lambda: called.append("worker") or 1)
    config.dyno("mailer", lambda: called.append("mailer") or 1)
    answers = iter([True, True, False])

    trace = sampler.round(
        [LOCAL, {"name": "mailer", "strategy": "jql"}], lambda: next(answers)
    )

    assert called == ["worker"]
    assert len(trace["ops"]) == 1


def test_a_logger_that_raises_does_not_end_the_round(config, sampler):
    class Broken:
        def error(self, message):
            raise OSError("closed stream")

    config.dyno("worker", raises(RuntimeError("down")))
    config.dyno("mailer", lambda: 3)
    config.logger = Broken()

    sampler.round([LOCAL, {"name": "mailer", "strategy": "jql"}], always)

    assert sampled(config) == {"mailer": {"jql": [3]}}


def test_samples_go_to_the_buffer_of_the_configuration_the_sampler_was_given():
    other = Configuration()
    other.dyno("worker", lambda: 7)

    Sampler(other).round([LOCAL], always)

    assert sampled(HireFire.configuration) == {}
    assert sampled(other) == {"worker": {"jql": [7]}}


def test_an_entry_with_a_problem_is_logged_once_and_not_sampled(
    config, sampler, load_macros, caplog
):
    called = []
    load_macros(
        rq=fake_macro(
            supports_plan_strategy=lambda strategy: False,
            job_queue_size=lambda *queues, **options: called.append(1) or 1,
        )
    )
    entries = [RQ_SIZE, {**RQ_SIZE, "name": "mailer"}, {"name": "x", "strategy": "rpm"}]

    sampler.round(entries, always)
    sampler.round(entries, always)

    assert called == []
    assert sampled(config) == {}
    assert messages(caplog) == [
        "[HireFire] Plan adapter 'rq' does not support strategy 'jqs' for 'worker'. "
        "Entry skipped.",
        "[HireFire] Plan adapter 'rq' does not support strategy 'jqs' for 'mailer'. "
        "Entry skipped.",
        "[HireFire] Unknown plan strategy 'rpm' for 'x'. Entry skipped.",
    ]
    assert caplog.records[0].levelno == logging.ERROR


def test_the_entries_without_a_problem_are_sampled_next_to_one_with_a_problem(
    config, sampler, load_macros, caplog
):
    load_macros(
        rq=fake_macro(job_queue_size=lambda *queues, **options: 6),
        celery=fake_macro(
            queues_required=lambda: True,
            job_queue_size=lambda *queues, **options: 99,
        ),
    )
    plan_entries = [
        {"name": "mailer", "strategy": "jqs", "adapter": "celery", "queues": []},
        {"name": "search", "strategy": "jqs", "adapter": "sidekiq"},
        RQ_SIZE,
    ]

    assert sampler.can_sample(plan_entries)
    sampler.round(plan_entries, always)

    assert sampled(config) == {"worker": {"jqs": [6]}}
    assert messages(caplog) == [
        "[HireFire] Plan adapter 'celery' for 'mailer' requires named queues. "
        "Entry skipped.",
        "[HireFire] Unknown plan adapter 'sidekiq' for 'search'. Entry skipped.",
    ]


def test_a_macro_that_fails_to_import_is_logged_once_for_all_its_entries(
    sampler, load_macros, caplog
):
    load_macros(rq=ImportError("No module named 'redis'"))

    sampler.round([RQ_SIZE, {**RQ_SIZE, "name": "mailer"}], always)

    assert messages(caplog) == [
        "[HireFire] Plan adapter 'rq' could not be imported "
        "(ImportError: No module named 'redis'). Its entries are skipped."
    ]


def test_a_plan_entry_without_a_local_sampler_of_its_name_warns_once(
    config, sampler, caplog
):
    config.dyno("workers", lambda: 1)

    sampler.round([LOCAL], always)
    sampler.round([LOCAL], always)

    assert sampled(config) == {}
    assert messages(caplog) == [
        "[HireFire] No config.dyno sampler is named 'worker', so this process "
        "samples nothing for it. Check the name, or select an adapter for it in "
        "the HireFire UI."
    ]
    assert caplog.records[0].levelno == logging.WARNING


def test_a_process_without_local_samplers_skips_a_local_entry_in_silence(
    config, sampler, caplog
):
    caplog.set_level(logging.DEBUG)

    sampler.round([LOCAL], always)

    assert sampled(config) == {}
    assert messages(caplog, logging.DEBUG) == []


def test_an_adapter_entry_records_what_the_macro_returns(config, sampler, load_macros):
    load_macros(rq=fake_macro(job_queue_size=lambda *queues, **options: 7))

    sampler.round([RQ_SIZE], always)

    assert sampled(config) == {"worker": {"jqs": [7]}}


def test_a_fraction_of_a_second_of_latency_is_recorded_as_a_float(
    config, sampler, load_macros
):
    load_macros(rq=fake_macro(job_queue_latency=lambda *queues, **options: 1.5))

    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)

    assert sampled(config) == {"worker": {"jql": [1.5]}}


def test_an_adapter_entry_wins_over_a_local_sampler_of_its_name_and_says_so_once(
    config, sampler, load_macros, caplog
):
    local = []
    config.dyno("worker", lambda: local.append(1) or 99)
    load_macros(rq=fake_macro(job_queue_size=lambda *queues, **options: 7))

    sampler.round([RQ_SIZE], always)
    sampler.round([RQ_SIZE], always)

    assert local == []
    assert sampled(config) == {"worker": {"jqs": [7]}}
    assert messages(caplog) == [
        "[HireFire] A HireFire UI adapter is configured for 'worker', so "
        "config.dyno('worker') with a local sampler is ignored. You can remove "
        "that local configuration. The UI adapter is used instead."
    ]
    assert caplog.records[0].levelno == logging.WARNING


def test_a_queue_list_over_the_limit_is_cut_and_logged_once_with_its_entry(
    config, sampler, load_macros, caplog
):
    seen = []
    load_macros(
        rq=fake_macro(
            job_queue_size=lambda *queues, **options: seen.append(queues) or 1
        )
    )
    names = [f"q{index}" for index in range(plan.MAX_QUEUES + 1)]

    sampler.round([{**RQ_SIZE, "queues": names}], always)
    sampler.round([{**RQ_SIZE, "queues": names}], always)

    assert seen == [tuple(names[:-1])] * 2
    assert messages(caplog) == [
        "[HireFire] Plan queue list for 'worker' truncated to 64 names."
    ]


def working_macro(
    size=lambda *queues, **options: 4, working=lambda *queues, **options: 3
):
    return fake_macro(
        plan_options=lambda strategy, options: options or {},
        job_queue_size=size,
        job_queue_latency=size,
        job_queue_working=working,
    )


def test_the_broker_that_plan_samples_read_is_logged_once_per_adapter_and_broker(
    config, sampler, load_macros, caplog
):
    caplog.set_level(logging.INFO)
    source = ["redis://***@host:6379/0"]
    load_macros(
        rq=fake_macro(
            job_queue_size=lambda *queues, **options: 7,
            plan_connection_options=lambda: {"redis_url": "redis://u:p@host:6379/0"},
            plan_source=lambda redis_url: source[0],
        )
    )

    sampler.round([RQ_SIZE, {**RQ_SIZE, "name": "mailer"}], always)
    sampler.round([RQ_SIZE], always)
    source[0] = "redis://other:6379/0"
    sampler.round([RQ_SIZE], always)

    assert messages(caplog, logging.INFO) == [
        "[HireFire] Plan samples for 'rq' read redis://***@host:6379/0.",
        "[HireFire] Plan samples for 'rq' read redis://other:6379/0.",
    ]


def test_latency_is_sampled_with_the_working_count(config, sampler, load_macros):
    load_macros(rq=working_macro())

    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)

    assert sampled(config) == {"worker": {"jql": [4], "wrk": [3]}}


def test_size_is_sampled_with_the_working_count_only_when_it_skips_working_jobs(
    config, sampler, load_macros
):
    load_macros(rq=working_macro())

    sampler.round([RQ_SIZE], always)
    assert sampled(config) == {"worker": {"jqs": [4]}}

    sampler.round([{**RQ_SIZE, "options": {"skip_working": True}}], always)
    assert sampled(config) == {"worker": {"jqs": [4], "wrk": [3]}}


def test_a_macro_without_a_working_count_samples_none(config, sampler, load_macros):
    load_macros(rq=fake_macro(job_queue_latency=lambda *queues, **options: 2))

    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)

    assert sampled(config) == {"worker": {"jql": [2]}}


def test_the_working_count_is_sampled_when_the_main_sample_is_invalid_or_raises(
    config, sampler, load_macros, caplog
):
    load_macros(rq=working_macro(size=lambda *queues, **options: -1))
    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)
    assert sampled(config) == {"worker": {"wrk": [3]}}

    load_macros(rq=working_macro(size=raises(RuntimeError("timeout"))))
    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)
    assert sampled(config) == {"worker": {"wrk": [3]}}

    assert messages(caplog) == [
        "[HireFire] Plan sampler for 'worker' returned int('-1'), "
        "expected a non-negative number. Sample dropped.",
        "[HireFire] Plan sampler for 'worker' raised RuntimeError: timeout",
    ]


def test_a_working_count_that_is_invalid_or_raises_leaves_the_main_sample(
    config, sampler, load_macros, caplog
):
    load_macros(rq=working_macro(working=lambda *queues, **options: "three"))
    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)
    assert sampled(config) == {"worker": {"jql": [4]}}

    load_macros(rq=working_macro(working=raises(RuntimeError("timeout"))))
    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)
    assert sampled(config) == {"worker": {"jql": [4]}}

    assert messages(caplog) == [
        "[HireFire] Plan working sampler for 'worker' returned str('three'), "
        "expected a non-negative number. Sample dropped.",
        "[HireFire] Plan working sampler for 'worker' raised RuntimeError: timeout",
    ]


def test_an_adapter_sample_that_is_not_ready_is_dropped_without_a_line(
    config, sampler, load_macros, caplog
):
    caplog.set_level(logging.DEBUG)
    not_ready = raises(SampleNotReadyError("still counting"))

    load_macros(rq=working_macro(size=not_ready))
    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)
    assert sampled(config) == {"worker": {"wrk": [3]}}

    load_macros(rq=working_macro(working=not_ready))
    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)
    assert sampled(config) == {"worker": {"jql": [4]}}

    assert messages(caplog, logging.DEBUG) == []


def test_an_adapter_sample_that_is_incomplete_is_logged(
    config, sampler, load_macros, caplog
):
    load_macros(
        rq=working_macro(size=raises(SampleIncompleteError("counts are stale")))
    )

    sampler.round([{**RQ_SIZE, "strategy": "jql"}], always)

    assert sampled(config) == {"worker": {"wrk": [3]}}
    assert messages(caplog) == [
        "[HireFire] Plan sampler for 'worker' raised "
        "SampleIncompleteError: counts are stale"
    ]


def test_an_adapter_sample_that_returns_after_the_round_ended_is_not_recorded(
    config, sampler, load_macros
):
    load_macros(rq=working_macro())
    answers = iter([True, False, False])

    sampler.round([{**RQ_SIZE, "strategy": "jql"}], lambda: next(answers))

    assert sampled(config) == {}


def test_a_hook_that_raises_while_an_entry_is_read_is_logged_for_that_entry(
    config, sampler, load_macros, caplog
):
    load_macros(
        rq=fake_macro(supports_plan_strategy=raises(RuntimeError("no settings"))),
        celery=fake_macro(job_queue_size=lambda *queues, **options: 2),
    )

    sampler.round([RQ_SIZE, {**RQ_SIZE, "name": "mailer", "adapter": "celery"}], always)

    assert sampled(config) == {"mailer": {"jqs": [2]}}
    assert messages(caplog) == [
        "[HireFire] Plan sampler for 'worker' raised RuntimeError: no settings"
    ]


def test_a_decimal_that_a_macro_returns_is_recorded_as_a_float(
    config, sampler, load_macros, caplog
):
    load_macros(rq=fake_macro(job_queue_size=lambda *queues, **options: Decimal("3")))

    sampler.round([RQ_SIZE], always)

    assert sampled(config) == {"worker": {"jqs": [3.0]}}
    assert messages(caplog) == []


def test_a_round_runs_inside_the_begin_and_end_hooks_of_the_macros(
    sampler, load_macros
):
    calls = []
    load_macros(
        rq=fake_macro(
            before_sample_job_queues=lambda: calls.append("before") or "token",
            after_sample_job_queues=lambda token: calls.append(f"after {token}"),
            job_queue_size=lambda *queues, **options: calls.append("sample") or 1,
        )
    )

    sampler.round([RQ_SIZE], always)

    assert calls == ["before", "sample", "after token"]


def test_a_round_returns_its_timing_with_one_entry_per_sampled_plan_entry(
    config, sampler, load_macros
):
    config.dyno("mailer", lambda: 1)
    load_macros(rq=fake_macro(job_queue_size=lambda *queues, **options: 1))
    entry = {**RQ_SIZE, "options": {"skip_working": True}}

    trace = sampler.round([entry, {"name": "mailer", "strategy": "jql"}], always)

    assert trace["wave_ms"] >= 0
    assert [{**op, "ms": 0} for op in trace["ops"]] == [
        {
            "adapter": "rq",
            "strategy": "jqs",
            "queues": ["default"],
            "options": {"skip_working": True},
            "ms": 0,
        },
        {"adapter": None, "strategy": "jql", "queues": [], "options": {}, "ms": 0},
    ]


def test_a_round_logs_its_timing_only_when_verbose_is_set(
    config, sampler, monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    config.dyno("worker", lambda: 1)

    sampler.round([LOCAL], always)
    assert messages(caplog, logging.INFO) == []

    monkeypatch.setenv("HIREFIRE_VERBOSE", "1")
    sampler.round([LOCAL], always)
    lines = messages(caplog, logging.INFO)
    assert lines[0].startswith("[HireFire] sample_job_queues wave_ms=")
    assert lines[1].startswith(
        "[HireFire] sample adapter=None strategy=jql queues= ms="
    )
