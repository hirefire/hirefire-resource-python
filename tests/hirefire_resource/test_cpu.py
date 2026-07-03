import os
from unittest.mock import patch

from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.cpu import CPU
from hirefire_resource.cpu.usage import Usage
from tests.helpers import at


def buffer():
    return HireFire.configuration.buffer


def reading(mapping):
    return lambda path: mapping.get(path)


def test_first_sample_only_seeds_the_baseline():
    with patch.object(Usage, "reading", return_value=(10.0, "cgroup_v2")), patch.object(
        Usage, "available_cpus", return_value=1.0
    ):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            assert collector.sample() is None
        assert buffer().flush()["cpu"] == {}


def test_second_sample_buffers_normalized_percentage():
    with patch.object(
        Usage, "reading", side_effect=[(10.0, "cgroup_v2"), (10.5, "cgroup_v2")]
    ), patch.object(Usage, "available_cpus", return_value=1.0):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
        with freeze_time(at(1001)):
            collector.sample()

        # 0.5 CPU-seconds over 1 wall-second on 1 available CPU => 50%.
        assert buffer().flush()["cpu"] == {"clock": {1001: [50.0]}}


def test_normalizes_by_available_cpus():
    with patch.object(
        Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (1.0, "cgroup_v2")]
    ), patch.object(Usage, "available_cpus", return_value=4.0):
        collector = CPU("worker")
        with freeze_time(at(1000)):
            collector.sample()
        with freeze_time(at(1001)):
            collector.sample()

        # 1 CPU-second over 1s on 4 CPUs => 25%.
        assert buffer().flush()["cpu"] == {"worker": {1001: [25.0]}}


def test_clamps_to_100_percent():
    with patch.object(
        Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (5.0, "cgroup_v2")]
    ), patch.object(Usage, "available_cpus", return_value=1.0):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
        with freeze_time(at(1001)):
            collector.sample()

        assert buffer().flush()["cpu"] == {"clock": {1001: [100.0]}}


def test_negative_usage_delta_skips_and_reseeds_the_baseline():
    with patch.object(
        Usage,
        "reading",
        side_effect=[(10.0, "cgroup_v2"), (5.0, "cgroup_v2"), (5.5, "cgroup_v2")],
    ), patch.object(Usage, "available_cpus", return_value=1.0):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
        # Source dropped 10.0 -> 5.0 between reads: skip, then re-baseline against 5.0.
        with freeze_time(at(1001)):
            assert collector.sample() is None
        with freeze_time(at(1002)):
            collector.sample()

        assert buffer().flush()["cpu"] == {"clock": {1002: [50.0]}}


def test_source_change_skips_and_reseeds_the_baseline():
    with patch.object(
        Usage,
        "reading",
        side_effect=[(10.0, "process"), (11.0, "cgroup_v2"), (11.5, "cgroup_v2")],
    ), patch.object(Usage, "available_cpus", return_value=1.0):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
        with freeze_time(at(1001)):
            assert collector.sample() is None
        with freeze_time(at(1002)):
            collector.sample()

        assert buffer().flush()["cpu"] == {"clock": {1002: [50.0]}}


def test_skips_sample_when_usage_unavailable():
    with patch.object(Usage, "reading", return_value=(None, None)), patch.object(
        Usage, "available_cpus", return_value=1.0
    ):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
        with freeze_time(at(1001)):
            assert collector.sample() is None
        assert buffer().flush()["cpu"] == {}


def test_non_positive_wall_delta_skips_the_sample():
    with patch.object(
        Usage, "reading", side_effect=[(10.0, "cgroup_v2"), (10.5, "cgroup_v2")]
    ), patch.object(Usage, "available_cpus", return_value=1.0):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
            assert collector.sample() is None
        assert buffer().flush()["cpu"] == {}


def test_skips_sample_when_available_cpus_is_none():
    with patch.object(
        Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (1.0, "cgroup_v2")]
    ), patch.object(Usage, "available_cpus", return_value=None):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
        with freeze_time(at(1001)):
            assert collector.sample() is None
        assert buffer().flush()["cpu"] == {}


def test_skips_sample_when_available_cpus_is_zero():
    with patch.object(
        Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (1.0, "cgroup_v2")]
    ), patch.object(Usage, "available_cpus", return_value=0.0):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            collector.sample()
        with freeze_time(at(1001)):
            assert collector.sample() is None
        assert buffer().flush()["cpu"] == {}


def test_recovers_after_an_initially_unavailable_usage_source():
    with patch.object(
        Usage,
        "reading",
        side_effect=[(None, None), (10.0, "cgroup_v2"), (10.5, "cgroup_v2")],
    ), patch.object(Usage, "available_cpus", return_value=1.0):
        collector = CPU("clock")
        with freeze_time(at(1000)):
            assert collector.sample() is None  # source down: no baseline
        with freeze_time(at(1001)):
            assert collector.sample() is None  # source back: seeds baseline
        with freeze_time(at(1002)):
            collector.sample()  # 0.5 over 1s on 1 CPU => 50%

        assert buffer().flush()["cpu"] == {"clock": {1002: [50.0]}}


def test_total_seconds_prefers_cgroup_v2():
    with patch.object(
        Usage,
        "read",
        side_effect=reading(
            {Usage.CGROUP_V2_USAGE: "usage_usec 2500000\nuser_usec 1000000"}
        ),
    ):
        assert abs(Usage.total_seconds() - 2.5) < 0.0001


def test_total_seconds_falls_back_to_cgroup_v1():
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CGROUP_V1_USAGE: "3000000000"})
    ):
        assert abs(Usage.total_seconds() - 3.0) < 0.0001


def test_reading_labels_the_active_source():
    with patch.object(
        Usage,
        "read",
        side_effect=reading({Usage.CGROUP_V2_USAGE: "usage_usec 2500000"}),
    ):
        seconds, source = Usage.reading()
        assert abs(seconds - 2.5) < 0.0001
        assert source == "cgroup_v2"


def test_reading_labels_the_source_it_falls_through_to():
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CGROUP_V1_USAGE: "3000000000"})
    ):
        seconds, source = Usage.reading()
        assert abs(seconds - 3.0) < 0.0001
        assert source == "cgroup_v1"


def test_total_seconds_falls_back_to_proc_namespace_sum():
    mapping = {
        "/proc/1/stat": "1 (ruby) S 0 1 1 0 -1 0 0 0 0 0 500 250 0 0 20 0 1 0 9 0 0",
        "/proc/2/stat": "2 (puma (worker)) S 1 1 1 0 -1 0 0 0 0 0 150 100 0 0 20 0 1 0 9 0 0",
    }
    with patch.object(Usage, "read", side_effect=reading(mapping)), patch(
        "hirefire_resource.cpu.usage.glob.glob", return_value=list(mapping)
    ), patch.object(Usage, "clock_ticks", return_value=100):
        # (500+250) + (150+100) = 1000 ticks / 100 = 10.0 seconds, whole-dyno.
        assert abs(Usage.total_seconds() - 10.0) < 0.0001


def test_proc_namespace_seconds_none_without_proc():
    with patch("hirefire_resource.cpu.usage.glob.glob", return_value=[]):
        assert Usage.proc_namespace_seconds() is None


def test_stat_ticks_parses_around_comm_with_spaces_and_parens():
    line = "4242 (rails (worker)) S 1 1 1 0 -1 0 0 0 0 0 500 250 0 0 20 0 1 0 100 0 0"
    assert Usage.stat_ticks(line) == 750


def test_total_seconds_falls_back_to_process_clock():
    with patch.object(Usage, "read", return_value=None), patch(
        "hirefire_resource.cpu.usage.glob.glob", return_value=[]
    ):
        assert isinstance(Usage.total_seconds(), float)


def test_available_cpus_reads_cgroup_v2_quota():
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CGROUP_V2_QUOTA: "50000 100000"})
    ):
        assert abs(Usage.available_cpus() - 0.5) < 0.0001


def test_available_cpus_ignores_unlimited_v2_quota():
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CGROUP_V2_QUOTA: "max 100000"})
    ):
        assert Usage.available_cpus() == os.cpu_count()


def test_available_cpus_reads_cgroup_v1_quota():
    mapping = {Usage.CGROUP_V1_QUOTA: "150000", Usage.CGROUP_V1_PERIOD: "100000"}
    with patch.object(Usage, "read", side_effect=reading(mapping)):
        assert abs(Usage.available_cpus() - 1.5) < 0.0001


def test_available_cpus_falls_back_to_processor_count():
    with patch.object(Usage, "read", return_value=None):
        assert Usage.available_cpus() == os.cpu_count()


def test_cedar_shared_1x_entitlement_from_memory_fingerprint(monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CEDAR_MEMORY_LIMIT: "536870912"})
    ):
        assert Usage.available_cpus() == 1.0


def test_cedar_shared_2x_entitlement_from_memory_fingerprint(monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CEDAR_MEMORY_LIMIT: "1073741824"})
    ):
        assert Usage.available_cpus() == 2.0


def test_cedar_dedicated_fingerprint_falls_through_to_processor_count(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CEDAR_MEMORY_LIMIT: "2684354560"})
    ):
        assert Usage.available_cpus() == os.cpu_count()


def test_entitlement_ignored_off_heroku():
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CEDAR_MEMORY_LIMIT: "536870912"})
    ):
        assert Usage.available_cpus() == os.cpu_count()


def test_cgroup_quota_wins_over_entitlement(monkeypatch):
    monkeypatch.setenv("DYNO", "web-5fb9c979-lft2l")
    mapping = {
        Usage.CGROUP_V2_QUOTA: "90000 100000",
        Usage.CEDAR_MEMORY_LIMIT: "536870912",
    }
    with patch.object(Usage, "read", side_effect=reading(mapping)):
        assert abs(Usage.available_cpus() - 0.9) < 0.0001


def test_render_entitlement_from_render_cpu_count(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_CPU_COUNT", "0.5")  # Render's fractional core count
    with patch.object(Usage, "read", side_effect=reading({})):
        assert abs(Usage.available_cpus() - 0.5) < 0.0001


def test_render_entitlement_ignored_off_render(monkeypatch):
    monkeypatch.setenv("RENDER_CPU_COUNT", "8")  # set, but RENDER unset
    with patch.object(Usage, "read", side_effect=reading({})):
        assert Usage.available_cpus() == os.cpu_count()


def test_render_without_a_cpu_count_falls_through_to_processor_count(monkeypatch):
    monkeypatch.setenv("RENDER", "true")  # RENDER set, but no RENDER_CPU_COUNT
    with patch.object(Usage, "read", side_effect=reading({})):
        assert Usage.available_cpus() == os.cpu_count()


def test_cgroup_quota_wins_over_render_entitlement(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_CPU_COUNT", "8")  # would be wrong if it won
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CGROUP_V2_QUOTA: "50000 100000"})
    ):
        assert abs(Usage.available_cpus() - 0.5) < 0.0001


def test_read_returns_stripped_file_contents(tmp_path):
    file = tmp_path / "usage"
    file.write_text(" 42\n")
    assert Usage.read(str(file)) == "42"


def test_read_returns_none_for_missing_path():
    assert Usage.read("/nonexistent/cgroup/file") is None


def test_read_returns_none_when_the_file_disappears_between_check_and_read():
    with patch("os.access", return_value=True), patch(
        "builtins.open", side_effect=OSError
    ):
        assert Usage.read("/proc/1/stat") is None


def test_clock_ticks_reads_sysconf():
    ticks = Usage.clock_ticks()
    assert isinstance(ticks, int)
    assert ticks > 0


def test_clock_ticks_falls_back_to_100():
    with patch("os.sysconf", side_effect=ValueError):
        assert Usage.clock_ticks() == 100


def test_cgroup_v2_without_a_usage_usec_line_falls_through_to_v1():
    mapping = {
        Usage.CGROUP_V2_USAGE: "user_usec 1000000\nsystem_usec 500000",
        Usage.CGROUP_V1_USAGE: "3000000000",
    }
    with patch.object(Usage, "read", side_effect=reading(mapping)):
        # The v2 file is present but malformed (no usage_usec line) => fall through.
        assert abs(Usage.total_seconds() - 3.0) < 0.0001


def test_available_cpus_ignores_v1_unlimited_quota():
    mapping = {Usage.CGROUP_V1_QUOTA: "-1", Usage.CGROUP_V1_PERIOD: "100000"}
    with patch.object(Usage, "read", side_effect=reading(mapping)):
        assert Usage.available_cpus() == os.cpu_count()


def test_stat_ticks_returns_none_for_a_line_without_a_comm_paren():
    assert Usage.stat_ticks("123 ruby S 0 1 1 0") is None


def test_stat_ticks_returns_none_for_a_truncated_line():
    assert Usage.stat_ticks("123 (ruby) S 0 1") is None


def test_proc_namespace_seconds_none_when_every_entry_is_unreadable():
    with patch(
        "hirefire_resource.cpu.usage.glob.glob",
        return_value=["/proc/1/stat", "/proc/2/stat"],
    ), patch.object(Usage, "read", return_value=None):
        # Files vanished between glob and read: nothing counted => None (not 0.0).
        assert Usage.proc_namespace_seconds() is None


def test_total_seconds_falls_through_on_malformed_cgroup_v2():
    # A non-numeric usage value must not raise. Fall through to the next source.
    mapping = {
        Usage.CGROUP_V2_USAGE: "usage_usec notanumber",
        Usage.CGROUP_V1_USAGE: "3000000000",
    }
    with patch.object(Usage, "read", side_effect=reading(mapping)):
        assert abs(Usage.total_seconds() - 3.0) < 0.0001


def test_available_cpus_falls_through_on_malformed_quota():
    # Garbage in cpu.max must not raise. Fall through to the processor count.
    with patch.object(
        Usage, "read", side_effect=reading({Usage.CGROUP_V2_QUOTA: "garbage 100000"})
    ):
        assert Usage.available_cpus() == os.cpu_count()
