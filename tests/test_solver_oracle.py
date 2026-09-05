"""The oracle comparison itself — master plan 6.5.

`app/solve/oracle.py` is the thing that decides whether two solvers agree, so a
test that ran it against two *real* solvers would only be as good as whichever
of them happened to be installed. What has to be pinned here is the judgement:
which quantities are judged, which are reported, and what happens when one side
refuses to answer. So the comparison is driven by **stub solvers** that return a
constructed `SolveOutput` — every difference is placed deliberately, at a known
size, on a known side of a known tolerance. That needs no CalculiX binary, which
is the honest way to test a comparator anyway: the comparator's bugs are not
CalculiX's bugs.

The one genuine end-to-end oracle run — a box in tension through both
`LinearStaticSolver` and `CalculiXSolver`, agreeing with each other *and* with
sigma = F/A — is at the bottom and skips when `ccx` is not on this machine. A
skip is not a pass and the skip reason says so.

`uniaxial_case` and `STEEL` are imported from `tests/test_solver.py` rather than
restated: it is the same textbook bar the in-house solver is already verified
against there, which is what makes the closed form quotable here. That module
opens no database and imports nothing heavy, so the import costs nothing.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from app.mesh.primitives import box_mesh
from app.mesh.types import TetMesh
from app.solve.base import SolveOutput, Solver
from app.solve.calculix.run import find_ccx
from app.solve.calculix.solver import CalculiXSolver
from app.solve.linear_static import LinearStaticSolver
from app.solve.oracle import (
    DISPLACEMENT_TOLERANCE,
    UNIFORM_FIELD_SPREAD,
    UNIFORM_STRESS_TOLERANCE,
    Agreement,
    Difference,
    compare,
    field_is_uniform,
)
from app.solve.types import LoadCase, SolverError, StaticResult
from tests.test_solver import STEEL, uniaxial_case

# -- stubs -------------------------------------------------------------------


def solve_output(
    *,
    volume_mm3: float = 1000.0,
    max_displacement_mm: float = 0.1,
    von_mises: NDArray[np.float64] | list[float] | None = None,
) -> SolveOutput:
    """A `SolveOutput` with the three compared quantities placed by hand.

    `max_von_mises_mpa` is taken from the field rather than passed separately:
    the oracle reads the summary for the difference and the array for
    uniformity, and a stub in which those two disagreed would be testing a state
    no solver can produce.
    """
    field = np.asarray(von_mises if von_mises is not None else [100.0] * 8, dtype=np.float64)
    result = StaticResult(
        max_displacement_mm=max_displacement_mm,
        max_displacement_node=0,
        max_von_mises_mpa=float(np.max(field)),
        max_von_mises_element=int(np.argmax(field)),
        factor_of_safety=10.0,
        yields=False,
        mass_kg=volume_mm3 * 1e-9 * STEEL.density_kg_m3,
        volume_mm3=volume_mm3,
        node_count=8,
        element_count=len(field),
        solve_seconds=0.0,
    )
    return SolveOutput(
        result=result,
        displacements=np.zeros((8, 3), dtype=np.float64),
        von_mises=field,
    )


class StubSolver(Solver):
    """A solver that answers with what it was constructed with."""

    def __init__(self, name: str, output: SolveOutput) -> None:
        self.name = name
        self.output = output
        self.calls = 0

    def solve(self, mesh: TetMesh, case: LoadCase) -> SolveOutput:
        self.calls += 1
        return self.output


class RefusingSolver(Solver):
    """A solver that declines the model, in its own words."""

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        self.message = message
        self.calls = 0

    def solve(self, mesh: TetMesh, case: LoadCase) -> SolveOutput:
        self.calls += 1
        raise SolverError(self.message)


UNIFORM = [100.0] * 12
#: Bulk at 100 MPa with a hot corner at 300 — a notch, and firmly non-uniform:
#: (300 - 100) / 300 is 0.667 against a 0.02 threshold.
NOTCHED = [100.0] * 11 + [300.0]


def bar() -> tuple[TetMesh, LoadCase]:
    """The mesh and case handed to both stubs.

    The stubs ignore both and the oracle does not look at either, but passing
    the real pair keeps every call in this file the call a caller would make.
    """
    return box_mesh((10.0, 10.0, 20.0), divisions=(1, 1, 2)), uniaxial_case(STEEL, 1000.0)


def difference(agreement: Agreement, name: str) -> Difference:
    """The one compared quantity by that name, or a failure if it is missing.

    Unpacking a one-element list rather than indexing: a comparison that
    stopped reporting a quantity, or reported it twice, must fail here rather
    than quietly assert against whichever one came first.
    """
    (found,) = [d for d in agreement.differences if d.name == name]
    return found


# -- agreement ---------------------------------------------------------------


class TestIdenticalSolversAgree:
    def test_two_identical_solvers_agree(self) -> None:
        mesh, case = bar()
        output = solve_output(von_mises=UNIFORM)
        agreement = compare(
            mesh, case, StubSolver("reference", output), StubSolver("candidate", output)
        )

        assert agreement.ran is True
        assert agreement.agrees is True
        assert agreement.reason == ""
        assert [d.name for d in agreement.differences] == [
            "volume_mm3",
            "max_displacement_mm",
            "max_von_mises_mpa",
        ]
        assert all(d.agrees is True for d in agreement.differences)

    def test_report_says_they_agree_and_names_both(self) -> None:
        mesh, case = bar()
        output = solve_output(von_mises=UNIFORM)
        report = compare(
            mesh, case, StubSolver("in-house", output), StubSolver("calculix", output)
        ).report()

        assert "in-house vs calculix: agree" in report
        assert "DISAGREE" not in report
        assert "UNMEASURED" not in report
        # Every compared quantity is in the report, not just the verdict: a
        # verdict with no numbers under it cannot be checked by a reader.
        for quantity in ("volume_mm3", "max_displacement_mm", "max_von_mises_mpa"):
            assert quantity in report


# -- volume: the precondition ------------------------------------------------


class TestVolumeIsThePrecondition:
    """A volume difference means the two were not given the same mesh, so
    nothing else in the comparison means anything. Zero tolerance."""

    def test_a_different_volume_is_a_disagreement(self) -> None:
        mesh, case = bar()
        reference = StubSolver("reference", solve_output(volume_mm3=1000.0))
        candidate = StubSolver("candidate", solve_output(volume_mm3=1000.001))

        agreement = compare(mesh, case, reference, candidate)

        assert agreement.agrees is False
        assert difference(agreement, "volume_mm3").agrees is False
        assert "DISAGREE" in agreement.report()

    def test_the_volume_tolerance_is_exactly_zero(self) -> None:
        mesh, case = bar()
        # One ulp apart. Read off the same mesh there is no reason for even
        # this, and a tolerance that let it through would let a genuinely
        # different mesh through too.
        larger = np.nextafter(1000.0, np.inf)
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(volume_mm3=1000.0)),
            StubSolver("candidate", solve_output(volume_mm3=float(larger))),
        )

        assert difference(agreement, "volume_mm3").tolerance == 0.0
        assert agreement.agrees is False

    def test_an_identical_volume_agrees(self) -> None:
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(volume_mm3=1234.5)),
            StubSolver("candidate", solve_output(volume_mm3=1234.5)),
        )

        assert difference(agreement, "volume_mm3").agrees is True


# -- displacement: judged tightly, always ------------------------------------


class TestDisplacementIsJudged:
    def test_outside_the_tolerance_disagrees(self) -> None:
        mesh, case = bar()
        reference = 0.1
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=reference)),
            StubSolver(
                "candidate",
                solve_output(max_displacement_mm=reference * (1.0 + 2.0 * DISPLACEMENT_TOLERANCE)),
            ),
        )

        assert difference(agreement, "max_displacement_mm").agrees is False
        assert agreement.agrees is False

    def test_just_inside_the_tolerance_agrees(self) -> None:
        mesh, case = bar()
        reference = 0.1
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=reference)),
            StubSolver(
                "candidate",
                solve_output(max_displacement_mm=reference * (1.0 + 0.5 * DISPLACEMENT_TOLERANCE)),
            ),
        )

        assert difference(agreement, "max_displacement_mm").agrees is True
        assert agreement.agrees is True

    def test_a_one_percent_displacement_error_is_a_fault_whatever_the_tolerance(
        self,
    ) -> None:
        """Stated in engineering terms rather than in terms of the constant.

        The two tests above are written against `DISPLACEMENT_TOLERANCE`, so
        they follow it wherever it is set and cannot notice it being loosened.
        This one does not: two direct solvers on the same matrix differ by their
        pivot order, which is parts in 1e-10, and a wrong load or a wrong
        restraint moves a displacement by orders of magnitude. Nothing
        legitimate lands at 1%, so 1% must be a disagreement no matter what the
        constant is set to.
        """
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=0.1)),
            StubSolver("candidate", solve_output(max_displacement_mm=0.101)),
        )

        assert difference(agreement, "max_displacement_mm").agrees is False
        assert agreement.agrees is False

    def test_two_direct_solvers_rounding_apart_still_agree(self) -> None:
        """The other end of the same band. SuperLU here and SPOOLES there
        factorise in different orders, so the last few digits differ; a
        tolerance tightened to machine precision would report that as a fault
        and the oracle would be unusable."""
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=0.1)),
            StubSolver("candidate", solve_output(max_displacement_mm=0.1 * (1.0 + 1e-10))),
        )

        assert difference(agreement, "max_displacement_mm").agrees is True

    def test_the_relative_difference_is_signed(self) -> None:
        """The direction is part of the finding, so it is not absolute."""
        mesh, case = bar()
        low = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=0.1)),
            StubSolver("candidate", solve_output(max_displacement_mm=0.09)),
        )
        high = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=0.1)),
            StubSolver("candidate", solve_output(max_displacement_mm=0.11)),
        )

        assert difference(low, "max_displacement_mm").relative == pytest.approx(-0.1)
        assert difference(high, "max_displacement_mm").relative == pytest.approx(+0.1)


# -- stress: reported in a non-uniform field, judged in a uniform one --------


class TestStressInANonUniformField:
    """The tolerance-shaped hole in every oracle.

    CalculiX extrapolates stress to the nodes and averages it there; the
    in-house solver reports the element's own constant value. At a notch the
    smoothed peak is legitimately lower, by an amount that depends on the mesh
    rather than on either solver being wrong. A comparison that judged that
    number would cry wolf on a correct integration, so it is reported instead.
    """

    def test_peak_stress_is_reported_not_judged(self) -> None:
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=NOTCHED)),
            StubSolver("candidate", solve_output(von_mises=[100.0] * 11 + [210.0])),
        )

        stress = difference(agreement, "max_von_mises_mpa")
        assert stress.agrees is None
        assert stress.tolerance is None

    def test_the_verdict_survives_a_large_stress_difference(self) -> None:
        mesh, case = bar()
        # 30% low — far outside anything that would be tolerated in a uniform
        # field, and exactly what smoothing at a concentration produces.
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=NOTCHED)),
            StubSolver("candidate", solve_output(von_mises=[100.0] * 11 + [210.0])),
        )

        assert agreement.ran is True
        assert agreement.agrees is True
        assert difference(agreement, "max_von_mises_mpa").relative == pytest.approx(-0.3)

    def test_the_field_is_recorded_as_non_uniform(self) -> None:
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=NOTCHED)),
            StubSolver("candidate", solve_output(von_mises=[100.0] * 11 + [210.0])),
        )

        assert agreement.uniform_field is False

    def test_the_report_says_the_stress_was_not_judged(self) -> None:
        """A reader must be able to tell a skipped check from a passed one."""
        mesh, case = bar()
        report = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=NOTCHED)),
            StubSolver("candidate", solve_output(von_mises=[100.0] * 11 + [210.0])),
        ).report()

        assert "reported" in report
        assert "not judged" in report

    def test_one_non_uniform_side_is_enough_to_stop_the_judgement(self) -> None:
        """Uniformity is a property of the comparison, not of one output: if
        either field has a concentration in it, smoothing is not the identity."""
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=UNIFORM)),
            StubSolver("candidate", solve_output(von_mises=NOTCHED)),
        )

        assert agreement.uniform_field is False
        assert difference(agreement, "max_von_mises_mpa").agrees is None


class TestStressInAUniformField:
    """In a uniform field nodal smoothing *is* the identity — every node
    carries the same tensor, so averaging changes nothing. A difference there
    has no legitimate source and is a real fault."""

    def test_a_stress_difference_in_a_uniform_field_fails(self) -> None:
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=[100.0] * 12)),
            StubSolver("candidate", solve_output(von_mises=[100.5] * 12)),
        )

        assert agreement.uniform_field is True
        stress = difference(agreement, "max_von_mises_mpa")
        assert stress.agrees is False
        assert stress.tolerance == UNIFORM_STRESS_TOLERANCE
        assert agreement.agrees is False
        assert "DISAGREE" in agreement.report()

    def test_a_stress_difference_just_inside_the_tolerance_agrees(self) -> None:
        mesh, case = bar()
        inside = 100.0 * (1.0 + 0.5 * UNIFORM_STRESS_TOLERANCE)
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=[100.0] * 12)),
            StubSolver("candidate", solve_output(von_mises=[inside] * 12)),
        )

        assert agreement.uniform_field is True
        assert difference(agreement, "max_von_mises_mpa").agrees is True
        assert agreement.agrees is True

    def test_a_one_percent_stress_error_in_a_uniform_field_is_a_fault(self) -> None:
        """The same band argument as displacement, for the quantity that is
        only judged here. In a uniform field the two solvers compute the same
        invariant from the same tensor, so 1% is not smoothing — it is a
        material, a unit or a component order read wrong."""
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=[100.0] * 12)),
            StubSolver("candidate", solve_output(von_mises=[101.0] * 12)),
        )

        assert agreement.uniform_field is True
        assert difference(agreement, "max_von_mises_mpa").agrees is False
        assert agreement.agrees is False

    def test_a_uniform_report_carries_no_not_judged_note(self) -> None:
        mesh, case = bar()
        report = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(von_mises=UNIFORM)),
            StubSolver("candidate", solve_output(von_mises=UNIFORM)),
        ).report()

        assert "not judged" not in report


# -- what "uniform" means ----------------------------------------------------


class TestFieldIsUniform:
    def test_a_flat_field_is_uniform(self) -> None:
        assert field_is_uniform(np.full(20, 137.0)) is True

    def test_a_notched_field_is_not(self) -> None:
        assert field_is_uniform(np.asarray(NOTCHED)) is False

    def test_the_threshold_is_the_documented_spread(self) -> None:
        peak = 100.0
        just_inside = peak * (1.0 - UNIFORM_FIELD_SPREAD)
        just_outside = peak * (1.0 - 2.0 * UNIFORM_FIELD_SPREAD)

        assert field_is_uniform(np.asarray([just_inside] * 9 + [peak])) is True
        assert field_is_uniform(np.asarray([just_outside] * 9 + [peak])) is False

    def test_an_all_zero_field_does_not_divide_by_zero(self) -> None:
        """An unloaded part has no stress anywhere, which is as uniform as a
        field gets. Reaching the division would be a ZeroDivisionError or a
        silent nan, and a nan compares False — so it would report a rest state
        as non-uniform."""
        assert field_is_uniform(np.zeros(10)) is True

    def test_it_uses_the_median_not_the_mean(self) -> None:
        """The statistic is the median on purpose, and this is the field that
        tells the two apart.

        Fifty elements sit at the 100 MPa peak and fifty-one at 97.5 — a real
        spread of 2.5%, outside the 2% threshold, so the field is not uniform
        and its stress must not be judged. The *mean* is dragged up to 98.74 by
        the hot half, giving a spread of 1.26% and declaring the same field
        uniform. Median 97.5 is the honest reading: more than half the material
        is that far off the peak.
        """
        field = np.asarray([100.0] * 50 + [97.5] * 51, dtype=np.float64)
        peak = float(np.max(field))

        by_mean = (peak - float(np.mean(field))) / peak
        by_median = (peak - float(np.median(field))) / peak
        assert by_mean <= UNIFORM_FIELD_SPREAD, "the mean would have called this uniform"
        assert by_median > UNIFORM_FIELD_SPREAD

        assert field_is_uniform(field) is False

    def test_a_hot_tail_cannot_buy_uniformity(self) -> None:
        """The physical version of the same rule: a bulk at 100 with a handful
        of hot elements at a load introduction stays non-uniform however hot
        they get. Under a mean the hot tail pulls the statistic towards the
        peak; under a median it cannot move it at all."""
        for hot in (200.0, 500.0, 5000.0):
            field = np.asarray([100.0] * 40 + [hot] * 3, dtype=np.float64)
            assert field_is_uniform(field) is False


# -- unmeasured is never a pass ----------------------------------------------


class TestAnOracleThatCouldNotRunIsUnmeasured:
    """`app/design/assertions.py`'s rule applied to the solver: a suite that
    skips what it could not read reports green on a part nobody checked."""

    def test_a_failing_candidate_is_unmeasured_and_not_a_pass(self) -> None:
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output()),
            RefusingSolver("calculix", "No CalculiX executable found. Looked on PATH."),
        )

        assert agreement.ran is False
        assert agreement.agrees is False
        assert agreement.differences == []

    def test_a_failing_reference_is_unmeasured_and_not_a_pass(self) -> None:
        """Both orders, because a comparator that only guards one of them
        reports agreement between one solver and nothing."""
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            RefusingSolver("in-house", "The model is under-constrained."),
            StubSolver("calculix", solve_output()),
        )

        assert agreement.ran is False
        assert agreement.agrees is False
        assert agreement.differences == []

    def test_the_report_carries_the_refusing_solvers_own_words(self) -> None:
        mesh, case = bar()
        words = "Increase element_size_mm to coarsen it"
        report = compare(
            mesh,
            case,
            StubSolver("in-house", solve_output()),
            RefusingSolver("calculix", words),
        ).report()

        assert "UNMEASURED" in report
        assert words in report
        assert "calculix could not run" in report
        assert "agree" not in report

    def test_the_reason_names_which_side_refused(self) -> None:
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            RefusingSolver("in-house", "singular system"),
            StubSolver("calculix", solve_output()),
        )

        assert agreement.reason.startswith("in-house could not run")
        assert "singular system" in agreement.reason

    def test_a_failed_reference_does_not_run_the_candidate(self) -> None:
        """There is nothing to compare against, and the candidate is the
        expensive one — a subprocess and a mesh written to disk."""
        mesh, case = bar()
        candidate = StubSolver("calculix", solve_output())

        compare(mesh, case, RefusingSolver("in-house", "singular"), candidate)

        assert candidate.calls == 0

    def test_an_unexpected_error_is_not_swallowed(self) -> None:
        """`SolverError` is the refusal a solver is allowed to make. Anything
        else is a bug in the integration, and turning it into a tidy
        `ran=False` would hide it behind the same words a missing binary
        produces."""

        class Broken(Solver):
            name = "broken"

            def solve(self, mesh: TetMesh, case: LoadCase) -> SolveOutput:
                raise KeyError("STRESS")

        mesh, case = bar()
        with pytest.raises(KeyError):
            compare(mesh, case, StubSolver("reference", solve_output()), Broken())


# -- zero references ---------------------------------------------------------


class TestZeroReference:
    def test_zero_against_zero_is_no_difference_at_all(self) -> None:
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=0.0)),
            StubSolver("candidate", solve_output(max_displacement_mm=0.0)),
        )

        displacement = difference(agreement, "max_displacement_mm")
        assert displacement.relative == 0.0
        assert displacement.agrees is True
        assert agreement.agrees is True

    def test_zero_to_non_zero_is_infinite_not_small(self) -> None:
        """A part that did not move against one that did is not a small
        relative change however small the absolute number — a restraint that
        was never applied looks exactly like this."""
        mesh, case = bar()
        agreement = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=0.0)),
            StubSolver("candidate", solve_output(max_displacement_mm=1e-12)),
        )

        displacement = difference(agreement, "max_displacement_mm")
        assert displacement.relative == float("inf")
        assert displacement.agrees is False
        assert agreement.agrees is False

    def test_an_infinite_difference_still_formats(self) -> None:
        """`report()` is what a human reads when this fires, so it must not be
        the thing that raises."""
        mesh, case = bar()
        report = compare(
            mesh,
            case,
            StubSolver("reference", solve_output(max_displacement_mm=0.0)),
            StubSolver("candidate", solve_output(max_displacement_mm=1e-12)),
        ).report()

        assert "DISAGREE" in report
        assert "inf" in report


# -- the genuine article -----------------------------------------------------


@pytest.mark.skipif(
    find_ccx() is None,
    reason=(
        "the CalculiX binary (ccx) is not installed on this machine, so the two "
        "solvers were never run against each other - this test did NOT pass, it "
        "was not measured"
    ),
)
class TestTheRealOracle:
    """One real comparison: a box in uniaxial tension, solved twice.

    Posed in a uniform stress field on purpose. That is where nodal smoothing
    is the identity, so the stress check is live rather than suspended — and it
    is also where there is a closed form, so the two agreeing with each other
    is not the only thing asserted. Two solvers can agree on the same wrong
    answer; neither can agree with sigma = F/A by accident.
    """

    width = 10.0
    depth = 20.0
    length = 100.0
    force = 5_000.0  # N

    @pytest.fixture(scope="class")
    def agreement(self) -> Agreement:
        mesh = box_mesh((self.width, self.depth, self.length), divisions=(2, 2, 8))
        return compare(
            mesh,
            uniaxial_case(STEEL, self.force),
            LinearStaticSolver(),
            CalculiXSolver(),
        )

    def test_the_two_solvers_agree(self, agreement: Agreement) -> None:
        assert agreement.ran is True, agreement.report()
        assert agreement.agrees is True, agreement.report()

    def test_the_field_was_uniform_so_the_stress_was_actually_judged(
        self, agreement: Agreement
    ) -> None:
        # Without this the stress agreement above would be vacuous.
        assert agreement.uniform_field is True, agreement.report()
        assert difference(agreement, "max_von_mises_mpa").agrees is True

    def test_both_match_the_closed_form_stress(self, agreement: Agreement) -> None:
        expected = self.force / (self.width * self.depth)  # sigma = F/A, MPa
        stress = difference(agreement, "max_von_mises_mpa")

        assert stress.reference == pytest.approx(expected, rel=1e-6)
        assert stress.candidate == pytest.approx(expected, rel=1e-3)

    def test_both_match_the_closed_form_extension(self, agreement: Agreement) -> None:
        area = self.width * self.depth
        expected = self.force * self.length / (area * STEEL.youngs_modulus_mpa)
        displacement = difference(agreement, "max_displacement_mm")

        # The headline peak includes the small Poisson contraction of the far
        # corner, so it sits just above the pure axial extension.
        assert displacement.reference == pytest.approx(expected, rel=5e-3)
        assert displacement.candidate == pytest.approx(expected, rel=5e-3)
