"""The mission ladder as a suite that runs — master plan 5.4.

Two halves, and the split is the same one `app/design/` keeps everywhere else.
Most of this is offline: the ladder's declaration, what makes a rung valid, and
how an outcome is decided are all pure and run with a fake runner in
milliseconds. Only `TestM1BuildsToTheClosedForm` reaches for the kernel, and it
imports it inside the test the way `test_kernel.py` does, so importing this
module does not drag ~166 MB of OCP into every collection.

Every number M1 claims is checked against a closed form, never against recorded
output — a bracket built to the wrong thickness and a bracket built to the right
one are the same picture, and only the arithmetic tells them apart.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pytest

from app.design.assertions import Assertion
from app.design.errors import SpecError
from app.design.missions import (
    LADDER,
    LadderReport,
    Mission,
    MissionOutcome,
    mission,
    run_ladder,
    run_mission,
)

# The ladder exactly as Decision 5 of the master plan states it. Written out here
# rather than read from the module under test, because a test that derives its
# expectation from the thing it checks agrees with any mistake that thing makes.
MASTER_PLAN_LADDER = (
    ("M1", "Machined bracket", "I"),
    ("M2", "Welded frame / bench", "III"),
    ("M3", "Sheet-metal enclosure", "IV"),
    ("M4", "Gearbox", "IV"),
    ("M5", "Sheet-metal stamping press", "V"),
    ("M6", "Belt conveyor system", "V"),
    ("M7", "6-axis robot arm", "VI"),
    ("M8", "Motorcycle chassis + swingarm", "VI"),
    ("M9", "Full vehicle chassis programme", "VII"),
)


class TestTheLadderIsTheMasterPlansLadder:
    """A rung must not leave the programme by being deleted from a list.

    The ladder is where Kryova's scope is written down. If a rung could vanish
    from this tuple, "the ladder is green" would quietly come to mean less over
    time and nothing would report it.
    """

    def test_every_rung_of_the_plan_is_declared(self) -> None:
        assert tuple((m.rung, m.title, m.era) for m in LADDER) == MASTER_PLAN_LADDER

    def test_the_rungs_are_in_tractability_order(self) -> None:
        """Order is the claim — M5 is the honest mid-point, not the fifth thing tried."""
        assert [m.rung for m in LADDER] == [f"M{n}" for n in range(1, 10)]

    def test_a_rung_can_be_fetched_by_name(self) -> None:
        assert mission("M1").title == "Machined bracket"

    def test_an_unknown_rung_lists_the_ones_there_are(self) -> None:
        with pytest.raises(KeyError, match="M1"):
            mission("M42")

    def test_the_rungs_that_are_buildable_today(self) -> None:
        """Coverage is measured, not asserted. This number moves as E18 lands.

        M2 joined it on 2026-09-06, when `app.assembly` gave the ladder its first
        rung that is a product rather than a part.
        """
        buildable = [m.rung for m in LADDER if m.buildable]
        assert buildable == ["M1", "M2"], (
            "If a rung became buildable, give it a spec or an assembly and its "
            "assertions, and update this test deliberately — it is the coverage figure."
        )

    def test_a_rung_that_builds_says_what_it_still_does_not_claim(self) -> None:
        """M2's geometry is checked and its welds are not sized. Both are facts."""
        assert mission("M1").unproven == ()
        assert any("E6" in caveat for caveat in mission("M2").unproven)

    def test_every_pending_rung_names_a_phase_that_owns_the_gap(self) -> None:
        """"Not yet buildable" with no reason is indistinguishable from forgotten."""
        for rung in LADDER:
            if rung.buildable:
                continue
            assert rung.needs, f"{rung.rung} is pending with no reason"
            assert all(
                need.startswith("E") for need in rung.needs
            ), f"{rung.rung} must name the phase that owns each gap"


class TestARungIsBuildableOrWaitingNeverBoth:
    """The four ways a declaration can be dishonest, each refused at construction."""

    def test_a_rung_with_no_design_and_no_reason_is_refused(self) -> None:
        with pytest.raises(SpecError, match="waiting for"):
            Mission(rung="M2", title="t", era="III", hard="h")

    def test_a_rung_cannot_be_both_buildable_and_waiting(self) -> None:
        with pytest.raises(SpecError, match="buildable or"):
            Mission(
                rung="M1",
                title="t",
                era="I",
                hard="h",
                spec=mission("M1").spec,
                assertions=mission("M1").assertions,
                needs=("E6 — something",),
            )

    def test_a_rung_that_builds_and_claims_nothing_is_refused(self) -> None:
        """A build with no assertions stays green while the geometry moves."""
        with pytest.raises(SpecError, match="claims nothing"):
            Mission(rung="M1", title="t", era="I", hard="h", spec=mission("M1").spec)

    def test_a_pending_rung_cannot_carry_assertions(self) -> None:
        with pytest.raises(SpecError, match="no design to check"):
            Mission(
                rung="M2",
                title="t",
                era="III",
                hard="h",
                needs=("E6 — a solver",),
                assertions=(
                    Assertion(name="n", measure="mass_kg", comparison="<=", bound=1.0),
                ),
            )

    def test_a_rung_must_be_named_like_a_rung(self) -> None:
        with pytest.raises(SpecError, match="M1..M9"):
            Mission(rung="bracket", title="t", era="I", hard="h", needs=("E6 — x",))


def _payload(**overrides: Any) -> dict[str, Any]:
    """A measurement payload that satisfies M1, so a test can spoil one field."""
    from app.design import missions as m

    base = {
        "volume_mm3": m._M1_VOLUME_MM3,
        "mass_kg": m._M1_MASS_KG,
        "surface_area_mm2": m._M1_AREA_MM2,
        "bounding_box_mm": {
            "size": [m._M1_WIDTH_MM, m._M1_DEPTH_MM, m._M1_THICK_MM]
        },
        "centre_of_mass_mm": [0.0, 0.0, m._M1_THICK_MM / 2.0],
        "solid_count": 1,
        "face_count": m._M1_FACES,
        "feature": "thing",
    }
    base.update(overrides)
    return base


def _runner_returning(payload: Mapping[str, Any]):
    def runner(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(payload)

    return runner


def _harness_ladder() -> tuple[Mission, ...]:
    """M1 and every rung nobody can build — the ladder a fake payload can answer for.

    M2 is left out here, and only here. It is an assembly: its claims are measured
    off real geometry through `app.assembly`, so a mock payload cannot satisfy it and
    its entirely correct failure would drown out what these tests are about, which is
    how a *pending* rung is reported. `tests/test_mission_m2.py` runs it for real.
    """
    return (mission("M1"), *[m for m in LADDER if not m.buildable])


class TestAPendingRungIsNeverAPass:
    """`assertions.UNMEASURED` one level up: a rung nobody climbed is not green."""

    def test_a_pending_rung_reports_pending_and_says_what_it_waits_on(self) -> None:
        result = run_mission(mission("M7"), _runner_returning(_payload()))

        assert result.outcome is MissionOutcome.PENDING
        assert not result.passed
        assert "multibody" in result.reason

    def test_a_pending_rung_is_never_built(self) -> None:
        """Building a rung we predicted cannot build spends time to learn nothing."""
        calls: list[str] = []

        def counting() -> Any:
            calls.append("made")
            return _runner_returning(_payload())

        run_ladder(counting)

        assert calls == ["made"] * 3, (
            "one runner for M1 and one for each of M2's two members, none for the "
            "seven pending rungs"
        )

    def test_pending_rungs_do_not_make_the_report_red(self) -> None:
        """A suite red until M9 lands is a suite somebody switches off."""
        report = run_ladder(lambda: _runner_returning(_payload()), _harness_ladder())

        assert report.ok
        assert len(report.pending) == 7

    def test_but_the_ladder_is_not_complete(self) -> None:
        """`ok` is the regression question; `complete` is the programme question."""
        report = run_ladder(lambda: _runner_returning(_payload()), _harness_ladder())

        assert not report.complete

    def test_the_sentence_a_human_reads_never_claims_full_coverage(self) -> None:
        """The failure this prevents: "9/9" read off a suite that ran one rung."""
        summary = run_ladder(
            lambda: _runner_returning(_payload()), _harness_ladder()
        ).summary()

        assert "1/8 rungs pass" in summary
        assert "7 not yet buildable" in summary


class TestARungThatClaimsToBuildAndDoesNotIsAFailure:
    """The declaration said it builds. If it does not, the declaration is false."""

    def test_a_build_that_stops_is_a_failure_naming_where(self) -> None:
        def broken(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            if tool == "catia_fillet":
                raise RuntimeError("radius exceeds the adjacent face")
            return _payload()

        result = run_mission(mission("M1"), broken)

        assert result.outcome is MissionOutcome.FAILED
        assert "radius exceeds" in result.reason
        assert result.build is not None and not result.build.ok

    def test_an_unimplemented_operation_is_a_failure_not_coverage(self) -> None:
        """Deliberately unlike `conformance.py`, which is asking a different question.

        There, a gap says which of two backends is behind. Here the rung *claimed*
        it builds, so an operation that regressed into unimplemented has falsified
        the claim — and calling it coverage would let a rung rot while green.
        """

        def unsupported(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            raise NotImplementedError(f"{tool} is not implemented on this backend")

        result = run_mission(mission("M1"), unsupported)

        assert result.outcome is MissionOutcome.FAILED

    def test_a_failed_assertion_fails_the_rung(self) -> None:
        result = run_mission(
            mission("M1"), _runner_returning(_payload(volume_mm3=1.0))
        )

        assert result.outcome is MissionOutcome.FAILED
        assert "volume" in result.reason

    def test_an_unmeasured_claim_fails_the_rung_too(self) -> None:
        """A payload missing a number has not verified the part — same rule as 5.1."""
        payload = _payload()
        del payload["face_count"]

        result = run_mission(mission("M1"), _runner_returning(payload))

        assert result.outcome is MissionOutcome.FAILED
        assert result.checks is not None and result.checks.unmeasured

    def test_one_failed_rung_makes_the_ladder_red(self) -> None:
        report = run_ladder(lambda: _runner_returning(_payload(volume_mm3=1.0)))

        assert not report.ok
        assert "REGRESSED" in report.summary()


class TestEachRungGetsItsOwnRunner:
    """A rung that passed on the previous rung's leftovers reports the right number
    for the wrong reason, and nothing in the arithmetic would show it."""

    def test_the_factory_is_called_once_per_buildable_rung(self) -> None:
        made: list[Any] = []

        def factory() -> Any:
            runner = _runner_returning(_payload())
            made.append(runner)
            return runner

        run_ladder(factory, [mission("M1"), mission("M1")])

        assert len(made) == 2, "two rungs, two runners — never one shared document"
        assert made[0] is not made[1]


class TestTheReportSerialises:
    def test_a_report_carries_the_pending_reasons_into_its_dict(self) -> None:
        """Whatever renders this must be able to say *why* a rung is not green."""
        data = run_ladder(
            lambda: _runner_returning(_payload()), _harness_ladder()
        ).to_dict()

        assert data["ok"] is True
        assert data["complete"] is False
        assert data["pending"] == 7
        m7 = next(r for r in data["results"] if r["rung"] == "M7")
        assert m7["outcome"] == "pending"
        assert any("E9" in need for need in m7["needs"])

    def test_an_empty_ladder_is_not_a_pass(self) -> None:
        """Truthiness on an empty report would make a mis-wired suite look green."""
        assert not LadderReport()
        assert not LadderReport().complete


class TestM1BuildsToTheClosedForm:
    """The actual regression test: the bracket, on the real kernel, every run.

    This is what 5.4 exists for. Everything above checks the harness; this checks
    the machine.
    """

    @staticmethod
    def _run():
        from app.kernel import OcctRunner

        return run_mission(mission("M1"), OcctRunner())

    def test_the_bracket_builds_and_every_claim_holds(self) -> None:
        result = self._run()

        assert result.outcome is MissionOutcome.PASSED, str(result)
        assert result.checks is not None
        assert len(result.checks.passed) == len(mission("M1").assertions)

    def test_it_is_the_twelve_call_plan_the_phase_one_proof_describes(self) -> None:
        result = self._run()

        assert result.build is not None
        assert len(result.build.completed) == 12

    def test_the_volume_is_the_closed_form_not_a_recorded_number(self) -> None:
        """w·d·t − 4·t·r²(1−π/4) − π(d/2)²·t, computed here independently."""
        from app.design import missions as m

        expected = (
            120.0 * 80.0 * 8.0
            - 4.0 * 8.0 * 5.0**2 * (1.0 - math.pi / 4.0)
            - math.pi * 7.0**2 * 8.0
        )
        result = self._run()

        assert result.build is not None
        got = result.build.last_result()["volume_mm3"]
        assert got == pytest.approx(expected, rel=1e-9)
        assert m._M1_VOLUME_MM3 == pytest.approx(expected, rel=1e-12)

    def test_the_material_actually_attached(self) -> None:
        """A mass that is right for steel on a part specified in aluminium is the
        failure `set_material` falls back to steel produces, and it is silent."""
        result = self._run()

        assert result.build is not None
        assert result.build.last_result()["density_kg_m3"] == pytest.approx(2700.0)

    def test_it_builds_the_same_part_twice(self) -> None:
        """Determinism (1.6) at the level a mission cares about: same spec, same part."""
        first, second = self._run(), self._run()

        assert first.build is not None and second.build is not None
        assert first.build.plan_digest == second.build.plan_digest
        assert first.build.last_result()["volume_mm3"] == pytest.approx(
            second.build.last_result()["volume_mm3"], rel=1e-12
        )

    def test_the_corner_fillet_is_scoped_to_the_slab(self) -> None:
        """Unscoped, `vertical` rounds every vertical edge the part ever grows.

        This is the mission-level pin for the defect the 2026-09-05 verification
        found in the design suite's own bracket fixture: `catia_fillet`'s `feature`
        argument was declared and silently dropped, so the canonical example of the
        vocabulary rounded the whole part and reported success. Nothing about M1 as
        it stands today can see that — the bracket has no second feature to catch —
        so the guard is measured against geometry that does.
        """
        from app.kernel import OcctRunner

        def build(scoped: bool) -> float:
            runner = OcctRunner()
            runner("catia_new_part", {"name": "P"})
            runner("catia_sketch_create", {"support": "XY", "name": "s"})
            runner(
                "catia_sketch_rectangle",
                {"sketch": "s", "width_mm": 120.0, "height_mm": 80.0},
            )
            runner("catia_pad", {"sketch": "s", "length_mm": 8.0, "name": "slab"})
            runner(
                "catia_plane_offset",
                {"reference": "XY", "distance_mm": 8.0, "name": "top"},
            )
            runner("catia_sketch_create", {"support": "top", "name": "bs"})
            runner(
                "catia_sketch_rectangle",
                {"sketch": "bs", "width_mm": 20.0, "height_mm": 20.0},
            )
            runner("catia_pad", {"sketch": "bs", "length_mm": 10.0, "name": "boss"})
            before = runner("catia_measure", {})["volume_mm3"]
            arguments: dict[str, Any] = {
                "radius_mm": 5.0,
                "edges": "vertical",
                "name": "c",
            }
            if scoped:
                arguments["feature"] = "slab"
            return before - runner("catia_fillet", arguments)["volume_mm3"]

        corner = 5.0**2 * (1.0 - math.pi / 4.0)

        assert build(scoped=True) == pytest.approx(4 * 8.0 * corner, rel=1e-9)
        assert build(scoped=False) == pytest.approx(
            4 * 8.0 * corner + 4 * 10.0 * corner, rel=1e-9
        ), "unscoped rounded the boss as well, and said it succeeded"

    def test_the_bore_order_is_readability_not_correctness(self) -> None:
        """The honest version of a claim this file first got wrong.

        The spec fillets before it drills because that is the machining order. It
        was originally justified as *necessary* — on the belief that `vertical`
        would otherwise catch the bore's seam — and that turned out not to be true
        here. Pinned so the false reason cannot come back as a bug report.
        """
        from app.kernel import OcctRunner

        def build(bore_first: bool) -> float:
            runner = OcctRunner()
            runner("catia_new_part", {"name": "P"})
            runner("catia_sketch_create", {"support": "XY", "name": "s"})
            runner(
                "catia_sketch_rectangle",
                {"sketch": "s", "width_mm": 120.0, "height_mm": 80.0},
            )
            runner("catia_pad", {"sketch": "s", "length_mm": 8.0, "name": "slab"})

            def drill() -> None:
                runner("catia_sketch_create", {"support": "XY", "name": "b"})
                runner("catia_sketch_circle", {"sketch": "b", "diameter_mm": 14.0})
                runner(
                    "catia_pocket",
                    {"sketch": "b", "limit": "up_to_last", "name": "bore"},
                )

            def round_corners() -> None:
                runner(
                    "catia_fillet",
                    {
                        "feature": "slab",
                        "radius_mm": 5.0,
                        "edges": "vertical",
                        "name": "c",
                    },
                )

            if bore_first:
                drill()
                round_corners()
            else:
                round_corners()
                drill()
            return runner("catia_measure", {})["volume_mm3"]

        assert build(bore_first=True) == pytest.approx(
            build(bore_first=False), rel=1e-12
        )
