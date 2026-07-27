"""Closed-world platform goldens for hirefire_resource.cpu.usage.Usage.

Fixture bodies are verbatim extracts from hirefire-resource/cpu-platform-samples.md
(capture date 2026-07-27). Do not invent platform samples here.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import patch

from hirefire_resource.cpu.usage import Usage

FIXTURE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "fixtures", "cpu")
)

# Loud default so host os.cpu_count never becomes a silent entitlement.
# Capture-meta nproc values (shared 8, Perf 2/8/4/8/16, Fir 48/96, Render 8/32)
# are documented in comments only; tests that care pass an explicit nproc=.
NPROC_SENTINEL = 97

# Cedar Performance / Private / Shield non-fingerprint sizes. Limits are real
# capture bodies (2026-07-27). Live nproc was M=2, L=8, L-RAM=4, XL=8, 2XL=16;
# tests stub NPROC_SENTINEL so a missed fingerprint still proves map-miss fallthrough.
CEDAR_DEDICATED = (
    "performance_m",
    "performance_l",
    "performance_l_ram",
    "performance_xl",
    "performance_2xl",
)

FIR_CPU_MAX = (
    ("dyno_1c_0_5gb_cpu_max.txt", 0.9),
    ("cpu_max_2c.txt", 1.8),
    ("cpu_max_4c.txt", 3.6),
    ("cpu_max_8c.txt", 7.2),
    ("cpu_max_16c.txt", 14.4),
    ("cpu_max_32c.txt", 28.8),
)

RENDER_PLAN_MATRIX = (
    ("free_cpu_max.txt", 0.15),
    ("starter_cpu_max.txt", 0.5),
    ("standard_cpu_max.txt", 1.0),
    ("pro_cpu_max.txt", 2.0),
    ("pro_plus_cpu_max.txt", 4.0),
    ("pro_max_cpu_max.txt", 4.0),
    ("pro_ultra_cpu_max.txt", 8.0),
)

RENDER_CPU_COUNT_STRINGS = (
    ("0.15", 0.15),
    ("0.50", 0.5),
    ("1", 1.0),
    ("8", 8.0),
)

DELTA = 0.0001


def fixture(relative_path: str) -> str:
    path = os.path.join(FIXTURE_ROOT, relative_path)
    with open(path) as handle:
        lines = [line for line in handle if not line.startswith("#")]
    return "".join(lines).strip()


@contextmanager
def closed_world(
    *,
    reads: dict[str, str] | None = None,
    proc_paths: list[str] | None = None,
    nproc: int = NPROC_SENTINEL,
    clock_ticks: int = 100,
):
    """Default every Usage.read → None (no host /proc or cgroup leak), inject
    only the fixture map. glob never sees the real host. process_seconds is
    None so usage never falls through to the process clock unless a test
    re-stubs. processor_count always stubs (default NPROC_SENTINEL).
    """
    mapping = dict(reads or {})
    paths = list(proc_paths or [])

    def fake_read(path: str) -> str | None:
        return mapping.get(path)

    with (
        patch.object(Usage, "read", side_effect=fake_read) as read_mock,
        patch("hirefire_resource.cpu.usage.glob.glob", return_value=paths),
        patch.object(Usage, "clock_ticks", return_value=clock_ticks),
        patch.object(Usage, "processor_count", return_value=nproc),
        patch.object(Usage, "process_seconds", return_value=None),
    ):
        yield read_mock


# --- Cedar (Heroku classic): entitlement ---


def test_cedar_basic_1x_fingerprint_not_host_nproc(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    # Capture nproc on shared Basic/1X was 8 (host). Fingerprint must win.
    with closed_world(
        reads={Usage.CEDAR_MEMORY_LIMIT: fixture("cedar/memory_limit_basic.txt")},
        nproc=8,
    ):
        assert abs(Usage.available_cpus() - 1.0) < DELTA


def test_cedar_standard_2x_fingerprint_not_host_nproc(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with closed_world(
        reads={Usage.CEDAR_MEMORY_LIMIT: fixture("cedar/memory_limit_standard_2x.txt")},
        nproc=8,
    ):
        assert abs(Usage.available_cpus() - 2.0) < DELTA


def test_cedar_performance_dedicated_fingerprint_miss_falls_to_nproc(monkeypatch):
    # Real limits from Performance (and matching Private/Shield) captures.
    # Expects prove CEDAR_MEMORY_LIMIT is read (fixture applied). NPROC_SENTINEL
    # proves the body is not in CEDAR_SHARED_ENTITLEMENTS (map miss → fallthrough).
    # Unread limit alone would also fall through to 97 without the expects.
    for name in CEDAR_DEDICATED:
        monkeypatch.setenv("DYNO", "web.1")
        body = fixture(f"cedar/memory_limit_{name}.txt")
        with closed_world(
            reads={Usage.CEDAR_MEMORY_LIMIT: body},
            nproc=NPROC_SENTINEL,
        ) as read_mock:
            assert (
                abs(Usage.available_cpus() - float(NPROC_SENTINEL)) < DELTA
            ), f"Cedar {name}: limit not in fingerprint map must fall through to nproc"
            read_mock.assert_any_call(Usage.CEDAR_MEMORY_LIMIT)


def test_cedar_private_s_and_shield_s_one_gib_fingerprint_no_space_special_case(
    monkeypatch,
):
    monkeypatch.setenv("DYNO", "run.8256")
    # High nproc so only fingerprint yields 2.0 (live Private/Shield-S nproc is 2).
    with closed_world(
        reads={Usage.CEDAR_MEMORY_LIMIT: fixture("cedar/memory_limit_standard_2x.txt")},
        nproc=8,
    ):
        assert abs(Usage.available_cpus() - 2.0) < DELTA


def test_cedar_dyno_unset_ignores_memory_fingerprint():
    # DYNO cleared by conftest. Shared 512 MiB limit must not fingerprint.
    with closed_world(
        reads={Usage.CEDAR_MEMORY_LIMIT: fixture("cedar/memory_limit_basic.txt")},
        nproc=8,
    ):
        assert abs(Usage.available_cpus() - 8.0) < DELTA


# --- Cedar: /proc usage ---


def test_cedar_basic_formation_puma_master_and_worker_proc_sum():
    master = fixture("cedar/proc_basic_formation_puma_master.txt")
    worker = fixture("cedar/proc_basic_formation_puma_worker.txt")
    paths = ["/proc/2/stat", "/proc/50/stat"]

    with closed_world(
        reads={
            "/proc/2/stat": master,
            "/proc/50/stat": worker,
        },
        proc_paths=paths,
        clock_ticks=100,
    ):
        # PID2: 3793+1400=5193, PID50: 80+15=95 → 5288 ticks / 100 = 52.88
        seconds, source = Usage.reading()
        assert source == "proc"
        assert abs(seconds - 52.88) < DELTA


def test_cedar_oneoff_zero_tick_ps_run_stays_on_proc():
    stat = fixture("cedar/proc_basic_oneoff_ps_run.txt")
    with closed_world(
        reads={"/proc/1/stat": stat},
        proc_paths=["/proc/1/stat"],
        clock_ticks=100,
    ):
        # closed_world nulls process_seconds; re-stub high to prove no fallthrough.
        with patch.object(Usage, "process_seconds", return_value=99.0):
            seconds, source = Usage.reading()
            assert source == "proc"
            assert abs(seconds - 0.0) < DELTA


# --- Fir (Heroku CNB) ---


def test_fir_dyno_1c_0_5gb_cpu_stat_usage():
    with closed_world(
        reads={
            Usage.CGROUP_V2_USAGE: fixture("fir/dyno_1c_0_5gb_cpu_stat.txt"),
        }
    ):
        seconds, source = Usage.reading()
        assert source == "cgroup_v2"
        assert abs(seconds - 31663 / 1_000_000.0) < DELTA


def test_fir_cpu_max_beats_host_nproc(monkeypatch):
    monkeypatch.setenv("DYNO", "run-nss86zptrv-7fpx8")
    # Capture host nproc was 96; trap with that value.
    with closed_world(
        reads={
            Usage.CGROUP_V2_QUOTA: fixture("fir/dyno_1c_0_5gb_cpu_max.txt"),
        },
        nproc=96,
    ):
        assert abs(Usage.available_cpus() - 0.9) < DELTA


def test_fir_parametric_unique_entitlements(monkeypatch):
    monkeypatch.setenv("DYNO", "web-fir-1")
    for file, expected in FIR_CPU_MAX:
        with closed_world(
            reads={Usage.CGROUP_V2_QUOTA: fixture(f"fir/{file}")},
            nproc=96,
        ):
            assert (
                abs(Usage.available_cpus() - expected) < DELTA
            ), f"Fir {file} should yield {expected}"


def test_fir_dyno_set_with_cpu_max_does_not_use_cedar_memory_limit(monkeypatch):
    monkeypatch.setenv("DYNO", "run-nss86zptrv-7fpx8")
    # Live Fir has no memory.limit_in_bytes path. Closed world leaves it nil.
    with closed_world(
        reads={
            Usage.CGROUP_V2_QUOTA: fixture("fir/dyno_1c_0_5gb_cpu_max.txt"),
        },
        nproc=96,
    ):
        assert Usage.read(Usage.CEDAR_MEMORY_LIMIT) is None
        assert abs(Usage.available_cpus() - 0.9) < DELTA


# --- Render ---


def test_render_starter_cpu_stat_usage():
    with closed_world(
        reads={Usage.CGROUP_V2_USAGE: fixture("render/starter_cpu_stat.txt")}
    ):
        seconds, source = Usage.reading()
        assert source == "cgroup_v2"
        assert abs(seconds - 858_123 / 1_000_000.0) < DELTA


def test_render_free_cpu_max_beats_marketing_0_1_env(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    # Marketing/docs say 0.1; live Free cpu.max is 0.15. Env must not win.
    monkeypatch.setenv("RENDER_CPU_COUNT", "0.1")
    with closed_world(
        reads={Usage.CGROUP_V2_QUOTA: fixture("render/free_cpu_max.txt")},
        nproc=8,
    ):
        assert abs(Usage.available_cpus() - 0.15) < DELTA


def test_render_free_render_cpu_count_without_cgroup(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_CPU_COUNT", "0.15")
    with closed_world(nproc=8):
        assert abs(Usage.available_cpus() - 0.15) < DELTA


def test_render_full_plan_matrix_cpu_max(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    # RENDER_CPU_COUNT left unset so only cpu.max can supply entitlement.
    for file, expected in RENDER_PLAN_MATRIX:
        with closed_world(
            reads={Usage.CGROUP_V2_QUOTA: fixture(f"render/{file}")},
            nproc=32,
        ):
            assert (
                abs(Usage.available_cpus() - expected) < DELTA
            ), f"Render {file} should yield {expected}"


def test_render_cpu_count_strings_without_cgroup(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    for raw, expected in RENDER_CPU_COUNT_STRINGS:
        monkeypatch.setenv("RENDER_CPU_COUNT", raw)
        with closed_world(nproc=32):
            assert (
                abs(Usage.available_cpus() - expected) < DELTA
            ), f"RENDER_CPU_COUNT={raw!r} should yield {expected}"


def test_render_quota_beats_misleading_render_cpu_count_low(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_CPU_COUNT", "0.1")
    with closed_world(
        reads={Usage.CGROUP_V2_QUOTA: fixture("render/starter_cpu_max.txt")},
        nproc=8,
    ):
        assert abs(Usage.available_cpus() - 0.5) < DELTA


def test_render_quota_beats_misleading_render_cpu_count_high(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_CPU_COUNT", "8")
    with closed_world(
        reads={Usage.CGROUP_V2_QUOTA: fixture("render/starter_cpu_max.txt")},
        nproc=8,
    ):
        assert abs(Usage.available_cpus() - 0.5) < DELTA


def test_render_pro_ultra_cpu_max_beats_host_nproc_32(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    # RENDER_CPU_COUNT unset: only cpu.max can yield 8.0 against nproc trap 32.
    with closed_world(
        reads={Usage.CGROUP_V2_QUOTA: fixture("render/pro_ultra_cpu_max.txt")},
        nproc=32,
    ):
        assert abs(Usage.available_cpus() - 8.0) < DELTA
