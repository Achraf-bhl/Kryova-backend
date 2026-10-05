"""A solve is admitted by the memory it needs (ROAD_TO_10 6.3).

The estimate's numbers are a measurement on one machine (module docstring of
`app/simulation/memory.py`), so the test of them says what it can say: never grossly *under* what
was measured, and the two regimes behave the way the measurements say they do. The governor is
tested with injected memory readers and a fake clock where time matters, so no result depends on
how much memory the machine running the suite happens to have. Everything runs offline except the
class that goes through the runner, which uses the same inline job queue as the other simulation
tests.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.core import hardware as hw
from app.core.hardware import Hardware
from app.simulation import memory, progress
from app.simulation.memory import (
    DEFAULT_MAX_ELEMENTS,
    FLOOR_MB,
    InsufficientMemory,
    MemoryGovernor,
    derive_element_limit,
    estimate_peak_mb,
    max_dof_for,
)
from app.solve.linear_static import ITERATIVE_THRESHOLD_DOF
from tests.test_agent import LOAD_CASE
from tests.test_simulation_waiting import (  # noqa: F401 - fixtures
    geometry,
    owner,
    project,
    queue,
    toolbox,
)
from tests.test_simulations import project_with_geometry  # noqa: F401 - fixture
from tests.test_simulations import run as _run
from tests.typing import AuthenticatedTestClient

#: (degrees of freedom, peak resident MB above the mesh's own), measured 2026-10-05, each size in
#: its own process: direct SuperLU on a tet10 box, then the CG path.
MEASURED = [
    (4_131, 47),
    (12_675, 257),
    (28_611, 692),
    (54_243, 1_912),
    (143_811, 1_465),
    (212_355, 2_185),
]


class TestTheEstimate:
    @pytest.mark.parametrize(("dof", "measured_mb"), MEASURED)
    def test_it_is_never_grossly_under_what_was_measured(self, dof: int, measured_mb: int) -> None:
        # A small solve is dominated by the interpreter and the arrays, which the floor covers,
        # so the lower bound is asserted everywhere and the upper bound only where it is not.
        assert estimate_peak_mb(dof).peak_mb >= 0.9 * measured_mb

    @pytest.mark.parametrize(("dof", "measured_mb"), [m for m in MEASURED if m[0] >= 12_000])
    def test_and_not_wildly_over_it_once_the_floor_stops_mattering(
        self, dof: int, measured_mb: int
    ) -> None:
        assert estimate_peak_mb(dof).peak_mb <= 1.5 * measured_mb

    def test_the_direct_formula_is_the_documented_one(self) -> None:
        # 50 + 1.6e-4 * 20,000^1.5 = 50 + 452.5; written out so a changed constant, exponent or
        # floor fails here and not only through a ratio that a changed floor leaves alone.
        assert estimate_peak_mb(20_000).peak_mb == 503
        assert estimate_peak_mb(0).peak_mb == 50

    def test_the_iterative_formula_is_the_documented_one(self) -> None:
        assert estimate_peak_mb(200_000).peak_mb == 2_150  # 50 + 1.05e-2 * 200,000

    def test_the_direct_path_grows_faster_than_the_problem(self) -> None:
        small, large = estimate_peak_mb(20_000), estimate_peak_mb(80_000)
        assert (small.path, large.path) == ("direct", "direct")
        # DOF x4 is memory x8 (n^1.5) once the constant floor is taken off.
        assert (large.peak_mb - FLOOR_MB) / (small.peak_mb - FLOOR_MB) == pytest.approx(8, rel=0.02)

    def test_the_iterative_path_grows_with_the_problem_and_no_faster(self) -> None:
        small, large = estimate_peak_mb(200_000), estimate_peak_mb(400_000)
        assert (small.path, large.path) == ("iterative", "iterative")
        assert (large.peak_mb - FLOOR_MB) / (small.peak_mb - FLOOR_MB) == pytest.approx(2, rel=0.02)

    def test_the_method_is_the_one_the_solver_will_use(self) -> None:
        assert estimate_peak_mb(ITERATIVE_THRESHOLD_DOF).path == "direct"
        assert estimate_peak_mb(ITERATIVE_THRESHOLD_DOF + 1).path == "iterative"

    def test_a_problem_just_over_the_threshold_needs_less_than_one_just_under_it(self) -> None:
        # Not a bug: the iterative method is the cheaper one, which is why it takes over.
        assert (
            estimate_peak_mb(ITERATIVE_THRESHOLD_DOF + 1).peak_mb
            < estimate_peak_mb(ITERATIVE_THRESHOLD_DOF).peak_mb
        )

    def test_the_estimate_says_what_it_rests_on_and_what_it_does_not_cover(self) -> None:
        basis = estimate_peak_mb(50_000).basis
        assert "measured" in basis and "CalculiX" in basis and "unmeasured" in basis

    def test_a_nonsense_size_is_the_floor_not_an_exception(self) -> None:
        assert estimate_peak_mb(-5).peak_mb == FLOOR_MB
        assert estimate_peak_mb(0).peak_mb == FLOOR_MB


def _governor(
    *,
    total: int | None = 10_000,
    available: int | None = 10_000,
    reserve: int = 1_000,
    available_fn=None,
) -> MemoryGovernor:
    return MemoryGovernor(
        total_mb=lambda: total,
        available_mb=available_fn or (lambda: available),
        reserve=lambda: reserve,
    )


class TestAdmission:
    def test_a_solve_that_fits_is_admitted_at_once_and_holds_its_memory_for_the_block(self) -> None:
        governor = _governor()
        with governor.admit(3_000, wait_s=0) as held:
            assert governor.reserved_mb == 3_000
            assert held.mb == 3_000 and held.basis == "measured" and held.waited_s < 0.5
        assert governor.reserved_mb == 0

    def test_the_memory_is_given_back_when_the_block_raises(self) -> None:
        governor = _governor()
        with pytest.raises(RuntimeError, match="solver blew up"):
            with governor.admit(3_000, wait_s=0):
                raise RuntimeError("solver blew up")
        assert governor.reserved_mb == 0

    def test_a_solve_larger_than_the_machine_is_refused_at_once_and_in_words(self) -> None:
        governor = _governor(total=4_000, available=4_000, reserve=1_000)
        started = time.monotonic()
        with pytest.raises(InsufficientMemory) as caught:
            with governor.admit(3_500, wait_s=60):
                pass
        assert time.monotonic() - started < 1.0, "it must not wait for memory that cannot exist"
        message = str(caught.value)
        assert "3,500 MB" in message and "4,000 MB" in message
        assert "cannot fit however long it waits" in message
        assert "element_size_mm" in message

    def test_the_reserve_counts_against_what_the_machine_can_ever_give(self) -> None:
        # 3,500 would fit a 4,000 MB machine with a 500 MB reserve and does not with 1,000.
        with _governor(total=4_000, available=4_000, reserve=500).admit(3_500, wait_s=0):
            pass
        with pytest.raises(InsufficientMemory):
            with _governor(total=4_000, available=4_000, reserve=1_000).admit(3_500, wait_s=0):
                pass

    def test_a_running_job_that_has_not_allocated_yet_is_still_counted(self) -> None:
        # Live `available` cannot see memory a job has been promised and not yet touched, so the
        # baseline minus what is reserved is the second constraint. Available stays at 10,000
        # throughout: only the reservation can stop the second job.
        governor = _governor(available=10_000, reserve=1_000)
        with governor.admit(6_000, wait_s=0):
            with pytest.raises(InsufficientMemory) as caught:
                with governor.admit(4_000, wait_s=0):
                    pass
            assert "held by runs already going" in str(caught.value)
            with governor.admit(3_000, wait_s=0):  # 10,000 - 6,000 - 1,000 = 3,000 free
                pass

    def test_what_a_running_job_has_really_allocated_is_not_counted_twice(self) -> None:
        # The first job has been promised 3,000 MB and has now touched all of it, so the live
        # reading has fallen by 3,000. Counting that fall *and* the reservation would call 6,000
        # taken; 10,000 - 3,000 - 1,000 = 6,000 is what is actually free, and 5,000 fits.
        live = {"available": 10_000}
        governor = _governor(available_fn=lambda: live["available"], reserve=1_000)
        with governor.admit(3_000, wait_s=0):
            live["available"] = 7_000
            with governor.admit(5_000, wait_s=0):
                pass

    def test_something_else_taking_memory_stops_a_solve_the_reservation_would_allow(self) -> None:
        # CATIA opens a large assembly: nothing in this governor reserved that, only the live
        # reading sees it. The first constraint alone would say 6,000 free.
        readings = iter([9_000, 3_500, 3_500, 3_500])
        governor = _governor(available_fn=lambda: next(readings), reserve=1_000)
        with governor.admit(3_000, wait_s=0):
            with pytest.raises(InsufficientMemory):
                with governor.admit(3_000, wait_s=0):
                    pass

    def test_the_baseline_is_forgotten_when_everything_has_been_given_back(self) -> None:
        # Otherwise a reading taken when the machine was busy would cap every later solve.
        level = {"mb": 3_000}
        governor = _governor(available_fn=lambda: level["mb"], reserve=500)
        with governor.admit(2_000, wait_s=0):
            pass
        level["mb"] = 10_000
        with governor.admit(8_000, wait_s=0):
            pass

    def test_memory_the_platform_will_not_report_is_admitted_and_said_to_be_unmeasured(
        self,
    ) -> None:
        governor = _governor(total=None, available=None)
        with governor.admit(50_000, wait_s=0) as held:
            assert held.basis == "unmeasured"

    def test_a_waiting_solve_runs_when_the_one_ahead_of_it_finishes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Polling is what catches other processes giving memory back; a release inside this
        # process must wake the waiter at once. With the poll stretched past the test, only the
        # wake-up can get the second solve in.
        monkeypatch.setattr(memory, "POLL_S", 30.0)
        governor = _governor(available=10_000, reserve=1_000)
        first_in = threading.Event()
        release_first = threading.Event()
        order: list[str] = []

        def first() -> None:
            with governor.admit(6_000, wait_s=5):
                order.append("first in")
                first_in.set()
                release_first.wait(5)
                order.append("first out")

        def second() -> None:
            first_in.wait(5)
            with governor.admit(6_000, wait_s=5) as held:
                order.append("second in")
                # Woken by the release, not by its own wait running out: that would be 5 s.
                assert 0 < held.waited_s < 2.0

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        first_in.wait(5)
        time.sleep(0.2)
        assert order == ["first in"], "the second must be waiting, not running beside it"
        release_first.set()
        for thread in threads:
            thread.join(5)
        assert order == ["first in", "first out", "second in"]

    def test_a_solve_that_waits_too_long_is_refused_and_says_how_long_it_waited(self) -> None:
        governor = _governor(available=10_000, reserve=1_000)
        with governor.admit(6_000, wait_s=0):
            started = time.monotonic()
            with pytest.raises(InsufficientMemory) as caught:
                with governor.admit(6_000, wait_s=0.3):
                    pass
            assert 0.25 <= time.monotonic() - started < 3.0
        message = str(caught.value)
        assert "it waited 0 s" in message or "waited" in message
        assert "Try again when they finish" in message

    def test_never_more_than_the_budget_runs_at_once_across_many_threads(self) -> None:
        governor = _governor(total=2_000, available=2_000, reserve=1_000)  # 1,000 MB to give
        lock = threading.Lock()
        running = {"now": 0, "max": 0, "done": 0}

        def job() -> None:
            with governor.admit(400, wait_s=30):
                with lock:
                    running["now"] += 1
                    running["max"] = max(running["max"], running["now"])
                time.sleep(0.05)
                with lock:
                    running["now"] -= 1
                    running["done"] += 1

        threads = [threading.Thread(target=job) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert running["done"] == 8
        assert running["max"] == 2  # 2 x 400 fits in 1,000; a third does not
        assert governor.reserved_mb == 0

    def test_the_caller_is_told_once_that_it_is_waiting_and_how_much_is_free(self) -> None:
        governor = _governor(available=10_000, reserve=1_000)
        told: list[int] = []
        with governor.admit(6_000, wait_s=0):
            with pytest.raises(InsufficientMemory):
                with governor.admit(6_000, wait_s=0.5, on_wait=told.append):
                    pass
        assert told == [3_000]

    def test_nobody_is_told_when_there_was_nothing_to_wait_for(self) -> None:
        told: list[int] = []
        with _governor().admit(1_000, wait_s=0, on_wait=told.append):
            pass
        assert told == []

    def test_a_notice_that_fails_cannot_be_the_reason_a_solve_did_not_run(self) -> None:
        governor = _governor(available=10_000, reserve=1_000)
        released = threading.Event()

        def hold() -> None:
            with governor.admit(6_000, wait_s=5):
                released.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        time.sleep(0.1)

        def broken(_free: int) -> None:
            released.set()  # let the holder go, then fail
            raise OSError("the status table is locked")

        with governor.admit(6_000, wait_s=5, on_wait=broken) as held:
            assert held.mb == 6_000
        holder.join(5)


class TestTheDerivedElementLimit:
    def test_a_twelve_gigabyte_machine_gets_a_limit_below_the_default(self) -> None:
        limit, basis = derive_element_limit(12 * 1024, 1_228)
        assert limit == 243_853
        assert "derived" in basis and "12,288 MB" in basis and "1,228 MB" in basis

    def test_a_large_machine_is_not_given_more_than_the_size_the_product_was_verified_at(
        self,
    ) -> None:
        assert derive_element_limit(256 * 1024, 25_600)[0] == DEFAULT_MAX_ELEMENTS == 400_000

    def test_a_small_machine_gets_a_small_limit_not_the_default(self) -> None:
        assert derive_element_limit(2_048, 1_024)[0] == 7_753

    def test_a_machine_that_will_not_say_keeps_the_default_and_says_it_did_not_derive(self) -> None:
        limit, basis = derive_element_limit(None, 1_024)
        assert limit == 400_000 and "unknown" in basis and not basis.startswith("derived")

    def test_a_linear_mesh_may_have_many_more_elements_than_a_quadratic_one(self) -> None:
        # The ratio is the whole reason the limit takes an order: one number for both would
        # refuse an eighth of what a tet4 mesh can hold.
        quadratic = derive_element_limit(3 * 1024, 512, 2)[0]
        linear = derive_element_limit(3 * 1024, 512, 1)[0]
        assert linear < DEFAULT_MAX_ELEMENTS, "the cap must not be what is being compared"
        assert linear > 5 * quadratic

    @pytest.mark.parametrize("total_mb", [1_500, 2_048, 4_096, 8_192, 16_384, 32_768])
    def test_the_limit_never_falls_when_the_machine_grows(self, total_mb: int) -> None:
        assert (
            derive_element_limit(total_mb * 2, 1_024)[0] >= derive_element_limit(total_mb, 1_024)[0]
        )

    @pytest.mark.parametrize("budget_mb", [60, 200, 800, 1_100, 3_000, 10_800, 40_000])
    def test_what_the_limit_allows_fits_the_budget_it_was_derived_from(
        self, budget_mb: int
    ) -> None:
        dof = max_dof_for(budget_mb)
        assert dof > 0
        assert estimate_peak_mb(dof).peak_mb <= budget_mb + 1  # +1: the estimate rounds

    @pytest.mark.parametrize("budget_mb", [60, 200, 800, 1_100, 1_200, 3_000, 10_800, 40_000])
    def test_and_it_uses_the_budget_rather_than_a_comfortable_part_of_it(
        self, budget_mb: int
    ) -> None:
        dof = max_dof_for(budget_mb)
        # `>=`, the mirror of the sibling's `+ 1`: the estimate rounds to whole MB, so at 60 MB a
        # model 2 % past the limit estimates 60.38 MB and reads back as exactly the budget.
        assert estimate_peak_mb(int(dof * 1.02) + 10).peak_mb >= budget_mb

    def test_the_direct_branch_never_reaches_the_threshold(self) -> None:
        # Why `max_dof_for` has no clamp on that branch: every budget the iterative branch does
        # not take stays well under the threshold on the direct path.
        for budget_mb in range(FLOOR_MB + 1, 1_200):
            dof = max_dof_for(budget_mb)
            if estimate_peak_mb(dof).path == "direct":
                assert dof < ITERATIVE_THRESHOLD_DOF, budget_mb

    def test_a_budget_that_does_not_cover_the_floor_allows_nothing(self) -> None:
        assert max_dof_for(FLOOR_MB) == 0
        assert max_dof_for(0) == 0

    def test_the_reserve_is_a_tenth_of_the_machine_and_never_under_a_gigabyte(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(memory, "settings", SimpleNamespace(memory_reserve_mb=None))
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(8, 4, 64 * 1024, "t", ()))
        assert memory.reserve_mb() == 6_553
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(8, 4, 4 * 1024, "t", ()))
        assert memory.reserve_mb() == 1_024

    def test_a_setting_beats_the_derived_reserve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(memory, "settings", SimpleNamespace(memory_reserve_mb=2_222))
        assert memory.reserve_mb() == 2_222

    def test_an_explicit_max_elements_is_used_as_given_even_where_the_machine_cannot_hold_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(2, 2, 2_048, "t", ()))
        monkeypatch.setattr(
            memory, "settings", SimpleNamespace(max_elements=900_000, memory_reserve_mb=None)
        )
        assert memory.element_limit() == 900_000
        assert memory.element_limit_basis() == "set explicitly (MAX_ELEMENTS)"

    def test_unset_it_comes_from_the_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(2, 2, 2_048, "t", ()))
        monkeypatch.setattr(
            memory, "settings", SimpleNamespace(max_elements=None, memory_reserve_mb=None)
        )
        assert memory.element_limit() == 7_753
        assert memory.element_limit_basis().startswith("derived")

    def test_the_size_the_refusal_suggests_depends_on_the_order_it_was_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.simulation.limits import finest_element_size_mm

        monkeypatch.setattr(hw, "hardware", lambda: Hardware(2, 2, 2_048, "t", ()))
        stats = {"bounding_box": {"size": [10.0, 20.0, 5.0]}}
        quadratic = finest_element_size_mm(stats, 2)
        linear = finest_element_size_mm(stats, 1)
        assert quadratic is not None and linear is not None
        assert linear < quadratic, "a linear mesh may be finer on the same machine"

    def test_analyses_the_estimate_does_not_cover_keep_the_old_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(2, 2, 2_048, "t", ()))
        monkeypatch.setattr(
            memory, "settings", SimpleNamespace(max_elements=None, memory_reserve_mb=None)
        )
        assert memory.default_element_limit() == 400_000
        monkeypatch.setattr(
            memory, "settings", SimpleNamespace(max_elements=123, memory_reserve_mb=None)
        )
        assert memory.default_element_limit() == 123


# -- through the runner ---------------------------------------------------------------------


class TestTheRunnerAdmitsBeforeItSolves:
    def test_a_run_that_fits_succeeds_and_gives_its_memory_back(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        governor = _governor(total=64_000, available=48_000, reserve=1_000)
        monkeypatch.setattr(memory, "GOVERNOR", governor)
        job = _run(auth_client, project_with_geometry)
        assert job["status"] == "succeeded", job["error"]
        assert governor.reserved_mb == 0

    def test_the_memory_is_held_while_the_solver_runs_not_before_or_after(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.solve.linear_static import LinearStaticSolver

        governor = _governor(total=64_000, available=48_000, reserve=1_000)
        monkeypatch.setattr(memory, "GOVERNOR", governor)
        held_during: list[int] = []
        real = LinearStaticSolver.solve

        def spy(self, mesh, case, **kwargs):
            held_during.append(governor.reserved_mb)
            return real(self, mesh, case, **kwargs)

        monkeypatch.setattr(LinearStaticSolver, "solve", spy)
        job = _run(auth_client, project_with_geometry)
        assert job["status"] == "succeeded", job["error"]
        assert held_during and held_during[0] >= FLOOR_MB
        assert governor.reserved_mb == 0

    def test_a_run_that_can_never_fit_fails_in_words_before_the_solver_is_called(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.solve.linear_static import LinearStaticSolver

        monkeypatch.setattr(
            memory, "GOVERNOR", _governor(total=1_100, available=1_100, reserve=1_090)
        )
        called: list[bool] = []
        monkeypatch.setattr(
            LinearStaticSolver,
            "solve",
            lambda *a, **k: called.append(True),  # type: ignore[arg-type,return-value]
        )
        job = _run(auth_client, project_with_geometry)
        assert job["status"] == "failed"
        assert "cannot fit however long it waits" in job["error"]
        assert "element_size_mm" in job["error"]
        assert called == []

    def test_a_run_that_waits_says_so_in_its_progress_and_is_refused_if_it_waits_too_long(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        governor = _governor(total=64_000, available=10_000, reserve=1_000)
        monkeypatch.setattr(memory, "GOVERNOR", governor)
        monkeypatch.setattr(memory.settings, "memory_wait_s", 0.4)
        details: list[str] = []
        real_report = progress.report

        def spy(session_scope, job_id, stage, *, detail="", **kwargs):
            details.append(detail)
            return real_report(session_scope, job_id, stage, detail=detail, **kwargs)

        monkeypatch.setattr(progress, "report", spy)
        with governor.admit(
            8_990, wait_s=0
        ):  # leaves 10 MB, and no solve costs less than the 50 MB floor
            job = _run(auth_client, project_with_geometry)
        assert job["status"] == "failed"
        assert "waited" in job["error"] and "held by runs already going" in job["error"]
        assert any(d.startswith("waiting for memory") for d in details)

    def test_a_convergence_study_admits_every_grid_on_its_own(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sizes: list[int] = []
        real = memory.admit_solve

        def spy(dof: int, **kwargs):
            sizes.append(dof)
            return real(dof, **kwargs)

        monkeypatch.setattr(memory, "admit_solve", spy)
        job = _run(auth_client, project_with_geometry, element_size_mm=8.0, grids=3)
        assert job["status"] == "succeeded", job["error"]
        assert len(sizes) == 3
        assert sizes == sorted(sizes), "each grid is finer than the one before"

    def test_a_derived_limit_refuses_before_meshing_and_names_where_it_came_from(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(2, 2, 2_048, "t", ()))
        job = _run(auth_client, project_with_geometry, element_size_mm=2.0)
        assert job["status"] == "failed"
        assert "over the 7,753 limit" in job["error"]
        assert "derived: 2,048 MB" in job["error"]

    def test_an_explicit_limit_is_not_described_as_derived(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(memory.settings, "max_elements", 100)
        job = _run(auth_client, project_with_geometry, element_size_mm=2.0)
        assert job["status"] == "failed"
        assert "over the 100 limit" in job["error"]
        assert "derived" not in job["error"]
        assert "set explicitly" not in job["error"], "a limit somebody typed needs no source note"


class TestTheRunnerAsksForTheRightLimitAndTheRightSize:
    def test_a_solve_is_admitted_at_three_degrees_of_freedom_per_node(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.solve.linear_static import LinearStaticSolver

        seen: dict[str, int] = {}
        real_admit = memory.admit_solve
        real_solve = LinearStaticSolver.solve

        def admit(dof: int, **kwargs):
            seen["dof"] = dof
            return real_admit(dof, **kwargs)

        def solve(self, mesh, case, **kwargs):
            seen["nodes"] = mesh.node_count
            return real_solve(self, mesh, case, **kwargs)

        monkeypatch.setattr(memory, "admit_solve", admit)
        monkeypatch.setattr(LinearStaticSolver, "solve", solve)
        job = _run(auth_client, project_with_geometry)
        assert job["status"] == "succeeded", job["error"]
        assert seen["dof"] == 3 * seen["nodes"]

    @pytest.mark.parametrize("grids", [1, 3])
    def test_a_linear_run_is_only_ever_asked_for_the_linear_limit(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
        grids: int,
    ) -> None:
        # Before the mesh, after the mesh and, for a study, on every grid: a request the route
        # accepted as linear must not be refused as quadratic by the worker, or the other way.
        orders: list[int] = []
        real = memory.element_limit

        def spy(element_order: int = 2) -> int:
            orders.append(element_order)
            return real(element_order)

        monkeypatch.setattr(memory, "element_limit", spy)
        job = _run(
            auth_client,
            project_with_geometry,
            element_order=1,
            element_size_mm=8.0 if grids > 1 else 10.0,
            grids=grids,
        )
        assert job["status"] == "succeeded", job["error"]
        assert orders and set(orders) == {1}


class TestTheOtherAnalysesKeepTheOldDefaultLimit:
    """Conduction, transient, flow and plane are not governed by the structural estimate, so
    `MAX_ELEMENTS` (else 400,000) is their limit whatever the machine's memory says."""

    def _bar_case(self) -> dict:
        from tests.test_simulations import TestAConductionAnalysisCanBeAskedFor

        return TestAConductionAnalysisCanBeAskedFor()._bar_case()

    def test_a_conduction_run_is_refused_over_the_setting(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(memory.settings, "max_elements", 1)
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={
                "analysis": "thermal-conduction",
                "thermal_case": self._bar_case(),
                "element_size_mm": 10.0,
            },
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["status"] == "failed"
        assert "over the 1 limit" in job["error"]


class TestTheAgentIsRefusedByTheSameLimitTheRouteUses:
    """The tool checks the request before the queue (ladder H4 run 8), and a limit that depends on
    the element order has to be asked with the order the run will use, or the tool and the runner
    disagree about the same request."""

    @pytest.fixture(autouse=True)
    def _a_small_machine_and_a_part_with_a_box(
        self, monkeypatch: pytest.MonkeyPatch, geometry, db_session
    ) -> None:
        monkeypatch.setattr(hw, "hardware", lambda: Hardware(2, 2, 2_048, "t", ()))
        # The shared fixture records a box with no size, which the check treats as "cannot say".
        geometry.stats = {"bounding_box": {"size": [10.0, 20.0, 5.0]}}
        db_session.flush()

    def test_a_quadratic_run_is_refused_where_a_linear_one_of_the_same_size_is_not(
        self, toolbox, queue
    ) -> None:
        from app.ai.tools import ToolError

        # 10 x 20 x 5 mm at 0.5 mm is about 48,000 tets: over the 7,753 a 2 GB machine holds
        # quadratic and under the ~55,000 it holds linear.
        request = {"load_case": LOAD_CASE, "element_size_mm": 0.5}
        with pytest.raises(ToolError, match=r"over the 7,753 limit"):
            toolbox.call("run_simulation", request, allow_mutations=True)
        queued = toolbox.call(
            "run_simulation", {**request, "element_order": 1}, allow_mutations=True
        )
        assert queued["status"] == "queued"
        assert len(queue.pending) == 1

    def test_the_refusal_names_the_size_that_would_have_fitted(self, toolbox) -> None:
        from app.ai.tools import ToolError

        with pytest.raises(ToolError, match=r"Use at least [\d.]+ mm"):
            toolbox.call(
                "run_simulation",
                {"load_case": LOAD_CASE, "element_size_mm": 0.5},
                allow_mutations=True,
            )
