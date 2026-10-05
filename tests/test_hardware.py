"""What this machine has, read once and said plainly (ROAD_TO_10 6.1, 6.2).

Everything here is offline and opens no database: the parsers take recorded text and the probe
takes an injected reader, so no result depends on the machine that happens to run the suite --
except the two tests that say so, which only assert what is true of *any* machine.

What it cannot say: the Windows reads (`GlobalMemoryStatusEx`, `GetLogicalProcessorInformation`)
have never run on Windows. Their parser is tested against a buffer built to the documented
layout, and the live call is THE QUEUE G9 item 1.
"""

from __future__ import annotations

import os
import struct

import pytest

from app.core import hardware as hw
from app.core.compute_plan import (
    MAX_DERIVED_WORKERS,
    MIN_THREADS_PER_JOB,
    derive,
    reserve_for,
)
from app.core.hardware import Hardware

MEMINFO = """\
MemTotal:       16123456 kB
MemFree:          812344 kB
MemAvailable:    9123456 kB
Buffers:          123456 kB
"""

# Two sockets, two physical cores each, two threads per core: 8 processors, 4 physical.
CPUINFO = "".join(
    f"processor\t: {n}\nphysical id\t: {n // 4}\ncore id\t\t: {(n // 2) % 2}\n\n" for n in range(8)
)


def _reader(files: dict[str, str]):
    return lambda path: files.get(path)


class TestReadingLinux:
    def test_total_and_available_memory_come_from_meminfo_in_megabytes(self) -> None:
        assert hw.parse_meminfo(MEMINFO) == (16123456 // 1024, 9123456 // 1024)

    def test_free_memory_is_not_called_available(self) -> None:
        # MemFree would call a machine with a warm page cache nearly full.
        total, available = hw.parse_meminfo(MEMINFO)
        assert available is not None and available > 812344 // 1024
        assert total is not None and available < total

    def test_a_meminfo_without_the_line_says_none_rather_than_zero(self) -> None:
        assert hw.parse_meminfo("MemFree: 1 kB\n") == (None, None)

    def test_physical_cores_are_distinct_socket_and_core_pairs_not_processors(self) -> None:
        assert hw.parse_physical_cores(CPUINFO) == 4

    def test_a_cpuinfo_with_no_core_ids_gives_no_count_rather_than_the_processor_count(self) -> None:
        text = "processor : 0\nmodel name : x\n\nprocessor : 1\nmodel name : x\n"
        assert hw.parse_physical_cores(text) is None

    def test_the_same_core_id_on_two_sockets_is_two_cores(self) -> None:
        text = (
            "processor : 0\nphysical id : 0\ncore id : 0\n\n"
            "processor : 1\nphysical id : 1\ncore id : 0\n\n"
        )
        assert hw.parse_physical_cores(text) == 2

    @pytest.mark.parametrize(
        ("text", "cores"),
        [("200000 100000", 2.0), ("50000 100000", 0.5), ("max 100000", None), ("", None), ("x y", None)],
    )
    def test_a_cpu_quota_is_read_as_cores_and_max_is_no_quota(self, text, cores) -> None:
        assert hw.parse_cpu_quota(text) == cores

    @pytest.mark.parametrize(
        ("text", "limit"),
        [
            ("4294967296\n", 4294967296),
            ("max\n", None),
            (None, None),
            ("", None),
            ("9223372036854771712\n", None),  # cgroup v1's spelling of "no limit"
        ],
    )
    def test_a_cgroup_memory_limit_is_bytes_and_no_limit_is_none(self, text, limit) -> None:
        assert hw.parse_cgroup_limit(text) == limit

    def test_a_container_limit_below_the_hosts_memory_wins_and_says_so(self) -> None:
        files = {
            "/proc/meminfo": MEMINFO,
            "/proc/cpuinfo": CPUINFO,
            "/sys/fs/cgroup/memory.max": str(2048 * 1024 * 1024),
        }

        found = hw.probe(_reader(files), platform="linux")

        assert found.total_ram_mb == 2048
        assert any("container memory limit of 2048 MB" in note for note in found.notes)

    def test_a_limit_above_the_hosts_memory_changes_nothing(self) -> None:
        files = {"/proc/meminfo": MEMINFO, "/sys/fs/cgroup/memory.max": str(10**12)}

        found = hw.probe(_reader(files), platform="linux")

        assert found.total_ram_mb == 16123456 // 1024
        assert not any("container" in note for note in found.notes)

    def test_a_cpu_quota_caps_the_logical_cores(self, monkeypatch) -> None:
        monkeypatch.setattr(os, "cpu_count", lambda: 16)
        monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(16)), raising=False)
        files = {"/proc/meminfo": MEMINFO, "/sys/fs/cgroup/cpu.max": "200000 100000"}

        found = hw.probe(_reader(files), platform="linux")

        assert found.logical_cores == 2
        assert any("CPU quota of 2" in note for note in found.notes)

    def test_an_affinity_mask_smaller_than_the_host_is_what_counts(self, monkeypatch) -> None:
        monkeypatch.setattr(os, "cpu_count", lambda: 64)
        monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0, 1, 2}, raising=False)

        found = hw.probe(_reader({"/proc/meminfo": MEMINFO}), platform="linux")

        assert found.logical_cores == 3
        assert any("limited to 3 of the host's 64" in note for note in found.notes)

    def test_physical_cores_never_exceed_the_cores_this_process_may_use(self, monkeypatch) -> None:
        monkeypatch.setattr(os, "cpu_count", lambda: 8)
        monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0, 1}, raising=False)
        files = {"/proc/meminfo": MEMINFO, "/proc/cpuinfo": CPUINFO}

        assert hw.probe(_reader(files), platform="linux").physical_cores == 2

    def test_missing_physical_cores_are_explained_not_blank(self) -> None:
        found = hw.probe(_reader({"/proc/meminfo": MEMINFO}), platform="linux")

        assert found.physical_cores is None
        assert any("physical core count is not reported" in note for note in found.notes)

    def test_available_memory_is_the_smaller_of_the_host_and_the_containers_headroom(self) -> None:
        files = {
            "/proc/meminfo": MEMINFO,
            "/sys/fs/cgroup/memory.max": str(2048 * 1024 * 1024),
            "/sys/fs/cgroup/memory.current": str(1536 * 1024 * 1024),
        }

        assert hw.available_ram_mb(_reader(files), platform="linux") == 512

    def test_available_memory_with_no_container_is_the_hosts(self) -> None:
        files = {"/proc/meminfo": MEMINFO}

        assert hw.available_ram_mb(_reader(files), platform="linux") == 9123456 // 1024

    def test_a_reader_that_finds_nothing_gives_none_not_an_exception(self) -> None:
        assert hw.available_ram_mb(_reader({}), platform="linux") is None
        assert hw.probe(_reader({}), platform="linux").total_ram_mb is None


class TestReadingWindows:
    @staticmethod
    def _entry(pointer: int, relationship: int) -> bytes:
        """One `SYSTEM_LOGICAL_PROCESSOR_INFORMATION`: mask, relationship padded, 16-byte union."""
        mask = b"\x01".ljust(pointer, b"\x00")
        kind = struct.pack("<I", relationship) + b"\x00" * (pointer - 4)
        return mask + kind + b"\x00" * 16

    def test_cores_are_the_relationship_zero_entries_on_a_64_bit_buffer(self) -> None:
        # Four cores, plus the cache and package entries a real call also returns.
        buffer = b"".join(self._entry(8, r) for r in (0, 0, 2, 0, 0, 3))

        assert hw.count_windows_cores(buffer, 8) == 4

    def test_the_32_bit_layout_is_24_bytes_an_entry(self) -> None:
        buffer = b"".join(self._entry(4, r) for r in (0, 0, 2))

        assert len(buffer) == 72
        assert hw.count_windows_cores(buffer, 4) == 2

    def test_a_buffer_that_is_not_whole_entries_is_refused_not_misread(self) -> None:
        assert hw.count_windows_cores(b"\x00" * 33, 8) is None
        assert hw.count_windows_cores(b"", 8) is None

    def test_a_buffer_with_no_core_entries_is_none(self) -> None:
        assert hw.count_windows_cores(self._entry(8, 2), 8) is None


class TestAnyMachine:
    def test_the_live_probe_reports_a_usable_machine(self) -> None:
        # True of every machine: at least one core, and memory if the platform says it.
        found = hw.probe()

        assert found.logical_cores >= 1
        assert found.total_ram_mb is None or found.total_ram_mb > 0
        assert found.physical_cores is None or 1 <= found.physical_cores <= found.logical_cores

    def test_the_probe_is_read_once_per_process(self) -> None:
        assert hw.hardware() is hw.hardware()

    def test_available_memory_is_asked_live_each_time(self) -> None:
        # Never cached: it changes by the second, and a stale figure admits a job that no longer fits.
        assert "lru_cache" not in repr(hw.available_ram_mb)


# -- the plan -------------------------------------------------------------------------------


def _machine(logical: int, physical: int | None) -> Hardware:
    return Hardware(logical, physical, 16384, "test")


class TestThePlan:
    @pytest.mark.parametrize(
        ("physical", "reserve"), [(1, 0), (2, 0), (3, 1), (8, 1), (9, 2), (64, 2)]
    )
    def test_cores_are_left_for_catia_and_the_system_on_all_but_the_smallest_machine(
        self, physical, reserve
    ) -> None:
        assert reserve_for(physical) == reserve

    @pytest.mark.parametrize("physical", [1, 2, 4, 6, 8, 12, 16, 32, 64, 128])
    def test_workers_times_threads_never_exceeds_the_budget_when_derived(self, physical) -> None:
        plan = derive(_machine(physical * 2, physical))

        assert plan.job_workers * plan.solver_threads <= max(physical - plan.reserved_cores, 1)
        assert not plan.oversubscribed

    @pytest.mark.parametrize("physical", [1, 2, 4, 8, 16, 64, 256])
    def test_a_derived_plan_always_runs_at_least_one_worker_and_never_too_many(self, physical) -> None:
        plan = derive(_machine(physical, physical))

        assert 1 <= plan.job_workers <= MAX_DERIVED_WORKERS

    def test_a_large_machine_is_capped_at_four_workers_because_meshing_serialises(self) -> None:
        # The literal, not MAX_DERIVED_WORKERS: a test that reads the cap from the module it
        # tests passes when the cap is changed to anything at all.
        plan = derive(Hardware(128, 64, 256 * 1024, "test", ()))
        assert plan.job_workers == 4
        assert plan.solver_threads == (64 - 2) // 4

    def test_each_worker_gets_its_share_of_the_budget_not_the_whole_of_it(self) -> None:
        plan = derive(Hardware(16, 8, 32 * 1024, "test", ()))
        assert (plan.job_workers, plan.solver_threads) == (2, 3)
        assert plan.job_workers * plan.solver_threads <= 8 - plan.reserved_cores

    def test_a_job_is_not_made_thinner_than_the_stated_minimum_when_workers_can_be_added(self) -> None:
        plan = derive(_machine(32, 16))  # budget 14

        assert plan.solver_threads >= MIN_THREADS_PER_JOB

    def test_a_small_machine_runs_one_worker_with_every_core_it_can_spare(self) -> None:
        plan = derive(_machine(8, 4))  # budget 3

        assert (plan.job_workers, plan.solver_threads, plan.reserved_cores) == (1, 3, 1)

    def test_unreported_physical_cores_are_assumed_halved_and_the_plan_says_it_assumed(self) -> None:
        plan = derive(_machine(12, None))

        assert plan.physical_cores == 6 and plan.physical_assumed
        assert "assumed" in plan.basis["job_workers"]

    def test_reported_physical_cores_are_not_called_assumed(self) -> None:
        plan = derive(_machine(12, 6))

        assert not plan.physical_assumed
        assert "assumed" not in plan.basis["job_workers"]

    def test_an_explicit_worker_count_wins_and_oversubscription_is_reported_not_prevented(self) -> None:
        plan = derive(_machine(8, 4), explicit_workers=6)

        assert plan.job_workers == 6
        assert plan.basis["job_workers"] == "set explicitly (JOB_WORKERS)"
        assert plan.oversubscribed

    def test_an_explicit_thread_count_wins(self) -> None:
        plan = derive(_machine(16, 8), explicit_threads=1)

        assert plan.solver_threads == 1
        assert plan.basis["solver_threads"] == "set explicitly"

    def test_a_nonsense_explicit_value_is_ignored_in_favour_of_the_derived_one(self) -> None:
        plan = derive(_machine(16, 8), explicit_workers=0, explicit_threads=-3)

        assert plan.job_workers >= 1 and plan.solver_threads >= 1
        assert "derived" in plan.basis["job_workers"]

    def test_every_figure_says_where_it_came_from(self) -> None:
        plan = derive(_machine(16, 8))

        assert set(plan.basis) == {"job_workers", "solver_threads"}
        assert all(plan.basis.values())


# -- the wiring (6.1 and 6.2): the probe and the plan reach the places that were typed numbers ----

import logging  # noqa: E402

from app import main as app_main  # noqa: E402
from app.core import compute_plan  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.jobs.queue import ThreadPoolJobQueue, get_job_queue  # noqa: E402
from app.models import StaffGrant, StaffRole  # noqa: E402
from app.solve import registry  # noqa: E402
from tests.typing import AuthenticatedTestClient  # noqa: E402

API = "/api/v1"

#: Eight physical cores, sixteen logical: reserve 1, budget 7, two workers of three threads.
EIGHT_CORES = Hardware(16, 8, 32 * 1024, "test", ())


@pytest.fixture
def eight_core_machine(monkeypatch: pytest.MonkeyPatch):
    """This process believes it is on `EIGHT_CORES`, with no setting pinning anything."""
    monkeypatch.setattr(hw, "hardware", lambda: EIGHT_CORES)
    monkeypatch.setattr(settings, "job_workers", None)
    monkeypatch.setattr(settings, "solver_threads", None)
    monkeypatch.setattr(settings, "inline_jobs", False)
    monkeypatch.setattr(settings, "job_queue_backend", "threadpool")
    compute_plan.reset()
    get_job_queue.cache_clear()
    yield EIGHT_CORES
    get_job_queue().shutdown()
    get_job_queue.cache_clear()
    compute_plan.reset()


class TestThePoolIsTheSizeThePlanDerived:
    def test_a_derived_plan_for_eight_cores_is_two_workers_of_three_threads(
        self, eight_core_machine
    ) -> None:
        plan = compute_plan.current()
        assert (plan.job_workers, plan.solver_threads) == (2, 3)

    def test_the_thread_pool_is_built_with_the_derived_worker_count(
        self, monkeypatch: pytest.MonkeyPatch, eight_core_machine
    ) -> None:
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(32, 16, 64 * 1024, "test", ()))
        compute_plan.reset()
        get_job_queue.cache_clear()
        queue = get_job_queue()
        assert isinstance(queue, ThreadPoolJobQueue)
        # 16 physical, 2 reserved, budget 14 // 3 = 4 workers (the cap).
        assert queue._pool._max_workers == 4

    def test_an_explicit_job_workers_setting_beats_the_derivation(
        self, monkeypatch: pytest.MonkeyPatch, eight_core_machine
    ) -> None:
        monkeypatch.setattr(settings, "job_workers", 5)
        compute_plan.reset()
        get_job_queue.cache_clear()
        queue = get_job_queue()
        assert isinstance(queue, ThreadPoolJobQueue)
        assert queue._pool._max_workers == 5

    def test_the_plan_is_read_once_so_a_pool_never_outlives_its_description(
        self, monkeypatch: pytest.MonkeyPatch, eight_core_machine
    ) -> None:
        first = compute_plan.current()
        monkeypatch.setattr(settings, "job_workers", 9)
        assert compute_plan.current() is first
        compute_plan.reset()
        assert compute_plan.current().job_workers == 9


class TestCalculiXIsToldHowManyThreadsItMayUse:
    def test_the_ccx_child_gets_the_derived_thread_count(self, eight_core_machine) -> None:
        solver = registry.build_solver("calculix")
        assert solver.threads == 3  # type: ignore[attr-defined]

    def test_an_explicit_solver_threads_setting_wins(
        self, monkeypatch: pytest.MonkeyPatch, eight_core_machine
    ) -> None:
        monkeypatch.setattr(settings, "solver_threads", 2)
        compute_plan.reset()
        assert registry.build_solver("calculix").threads == 2  # type: ignore[attr-defined]

    def test_the_in_house_solver_is_unchanged_because_its_blas_is_fixed_at_import(
        self, eight_core_machine
    ) -> None:
        # BLAS threads in this process are set when numpy loads; nothing here can narrow them
        # per job, and a field that pretended to would be a promise the solver cannot keep.
        solver = registry.build_solver("internal")
        assert not hasattr(solver, "threads")


class TestTheStartupLogSaysWhatWasChosen:
    def test_it_logs_the_machine_and_the_plan_with_where_each_figure_came_from(
        self, caplog: pytest.LogCaptureFixture, eight_core_machine
    ) -> None:
        with caplog.at_level(logging.INFO, logger=app_main.logger.name):
            app_main._log_compute_plan()
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "hardware: 16 logical / 8 physical cores, 32768 MB RAM (test)" in text
        assert "compute plan: 2 job worker(s) x 3 solver thread(s)" in text
        assert "derived" in text

    def test_an_oversubscribing_setting_is_warned_about_and_still_obeyed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        eight_core_machine,
    ) -> None:
        monkeypatch.setattr(settings, "job_workers", 9)
        compute_plan.reset()
        with caplog.at_level(logging.INFO, logger=app_main.logger.name):
            app_main._log_compute_plan()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("oversubscribes" in r.getMessage() for r in warnings)
        assert compute_plan.current().job_workers == 9

    def test_a_probe_that_raises_cannot_stop_the_boot(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def boom() -> Hardware:
            raise RuntimeError("no such counter")

        monkeypatch.setattr(hw, "hardware", boom)
        compute_plan.reset()
        try:
            app_main._log_compute_plan()  # must not raise
        finally:
            compute_plan.reset()
        assert any("Could not read this machine's hardware" in r.getMessage() for r in caplog.records)


class TestTheAdminConsoleReadsIt:
    def _staff(self, db_session, user_id: str) -> None:
        db_session.add(StaffGrant(user_id=user_id, role=StaffRole.SUPPORT))
        db_session.flush()

    def test_health_carries_the_machine_the_free_memory_and_the_plan(
        self, auth_client: AuthenticatedTestClient, db_session, current_user_id: str,
        eight_core_machine,
    ) -> None:
        self._staff(db_session, current_user_id)
        body = auth_client.get(f"{API}/admin/health").json()["compute"]
        assert body["hardware"]["logical_cores"] == 16
        assert body["hardware"]["physical_cores"] == 8
        assert body["hardware"]["total_ram_mb"] == 32 * 1024
        assert body["plan"]["job_workers"] == 2
        assert body["plan"]["solver_threads"] == 3
        assert body["plan"]["oversubscribed"] is False
        assert "derived" in body["plan"]["basis"]["job_workers"]
        assert body["available_ram_mb"] is None or body["available_ram_mb"] >= 0

    def test_an_explicit_setting_is_reported_as_explicit(
        self, monkeypatch: pytest.MonkeyPatch, auth_client: AuthenticatedTestClient,
        db_session, current_user_id: str, eight_core_machine,
    ) -> None:
        monkeypatch.setattr(settings, "job_workers", 9)
        compute_plan.reset()
        self._staff(db_session, current_user_id)
        plan = auth_client.get(f"{API}/admin/health").json()["compute"]["plan"]
        assert plan["job_workers"] == 9
        assert plan["oversubscribed"] is True
        assert plan["basis"]["job_workers"] == "set explicitly (JOB_WORKERS)"

    def test_the_autoscale_policy_divides_by_the_pool_size_in_force(
        self, monkeypatch: pytest.MonkeyPatch, auth_client: AuthenticatedTestClient,
        db_session, current_user_id: str, eight_core_machine,
    ) -> None:
        from app.jobs import autoscale

        seen: dict[str, int] = {}
        real = autoscale.recommend

        def spy(snapshot, policy, **kwargs):
            seen["jobs_per_worker"] = policy.jobs_per_worker
            return real(snapshot, policy, **kwargs)

        monkeypatch.setattr(autoscale, "recommend", spy)
        # 3, not the derived 2: a policy that still divided by a typed 2 would agree with the
        # derivation on this machine and disagree with any setting.
        monkeypatch.setattr(settings, "job_workers", 3)
        compute_plan.reset()
        self._staff(db_session, current_user_id)
        assert auth_client.get(f"{API}/admin/compute/scaling").status_code == 200
        assert seen["jobs_per_worker"] == 3
