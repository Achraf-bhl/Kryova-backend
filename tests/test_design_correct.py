"""The self-correction loop: build, check, diagnose, repair, repeat.

C4. The tests are about *stopping*, because deciding when to give up is the hard
half and an unbounded retry loop is the documented way an agent burns a budget
without converging.

* **A repair that builds the same part ends it.** The compiler is
  deterministic, so an identical plan is an identical outcome — this is an exact
  test, not a heuristic, and it is the cheapest one available.
* **A plan already tried ends it.** Two designs the repairer oscillates between
  would otherwise run to the cap every time.
* **The attempt cap is real** and is the backstop when neither of the above
  fires.
* **A diagnosis says why, not just that.** A loop whose feedback is "it failed"
  is a retry counter; the repairer is handed the measured value, the bound, the
  gap and the parameters it is actually allowed to change.

Offline: no database fixture, no CATIA, no gmsh, no model.
"""

from collections.abc import Mapping
from typing import Any

import pytest

from app.design.assertions import Assertion
from app.design.compile import Plan
from app.design.correct import Diagnosis, Stop, correct
from app.design.execute import BuildReport, execute_plan
from app.design.spec import DesignSpec, FeatureSpec
from tests.test_design_compile import bracket

#: kg/mm³, roughly steel. Only needs to be consistent, not right.
DENSITY = 7.85e-6

#: The fixture bracket is 120 x 80 x 8 mm, so about 0.60 kg. A budget of 0.4
#: fails at first and is reachable by thinning the plate, which is what makes
#: the converging tests below actually converge.
BUDGET = [Assertion("mass budget", "mass_kg", "<=", 0.40, note="Weight budget is 0.4 kg.")]


def mass_of(plan: Plan) -> float:
    p = plan.parameters
    return p.number("width_mm") * p.number("depth_mm") * p.number("thick_mm") * DENSITY


def build(plan: Plan) -> BuildReport:
    """A builder whose mass responds to the design, so repairs can work."""
    mass = round(mass_of(plan), 6)

    def runner(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"feature": f"{tool.removeprefix('catia_').title()}.1", "mass_kg": mass}

    return execute_plan(plan, runner)


def failing_build(plan: Plan) -> BuildReport:
    def runner(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if tool == "catia_pad":
            raise RuntimeError("The sketch is not closed, so the pad has nothing to extrude.")
        return {"feature": f"{tool}.1"}

    return execute_plan(plan, runner)


def thin_by(step: float):
    """A repairer that thins the plate, which genuinely reduces the mass."""

    def repair(spec: DesignSpec, diagnosis: Diagnosis) -> DesignSpec | None:
        return spec.set_parameter("thick_mm", diagnosis.settable["thick_mm"] - step)

    return repair


class TestItStopsWhenItIsRight:
    def test_a_design_that_already_passes_runs_once(self) -> None:
        generous = [Assertion("mass", "mass_kg", "<=", 10.0)]

        report = correct(bracket(), build=build, repair=thin_by(1.0), assertions=generous)

        assert report.stop is Stop.SATISFIED
        assert report
        assert len(report) == 1
        assert not report.changed()

    def test_it_repairs_until_the_assertion_passes(self) -> None:
        report = correct(
            bracket(), build=build, repair=thin_by(2.0), assertions=BUDGET, max_attempts=5
        )

        assert report.stop is Stop.SATISFIED
        assert report.changed()
        assert len(report) == 3, "8mm -> 6mm -> 4mm is where it comes under budget"

    def test_the_repaired_spec_is_what_comes_back(self) -> None:
        report = correct(
            bracket(), build=build, repair=thin_by(2.0), assertions=BUDGET, max_attempts=5
        )

        assert report.spec is not None
        assert report.spec.parameters.resolve().number("thick_mm") == 4.0

    def test_the_summary_says_what_it_changed(self) -> None:
        report = correct(
            bracket(), build=build, repair=thin_by(2.0), assertions=BUDGET, max_attempts=5
        )

        summary = report.summary()
        assert "satisfied after 3 attempts" in summary
        assert "thick_mm" in summary
        assert summary.count("Bracket") == 1, "the design name should not be doubled"

    def test_a_build_with_no_assertions_is_satisfied_by_building(self) -> None:
        """Nothing was claimed about the part, so nothing can be checked."""
        report = correct(bracket(), build=build, repair=thin_by(1.0))

        assert report.stop is Stop.SATISFIED
        assert len(report) == 1


class TestItStopsWhenItIsGettingNowhere:
    def test_a_repair_that_builds_the_same_part_ends_it_immediately(self) -> None:
        """Same plan, same geometry, same failure. Running it again learns nothing."""
        report = correct(
            bracket(), build=build, repair=lambda s, d: s, assertions=BUDGET, max_attempts=9
        )

        assert report.stop is Stop.NO_PROGRESS
        assert len(report) == 1, "the no-op must not be run as an attempt"

    def test_rewriting_only_a_note_counts_as_no_progress(self) -> None:
        """The case a plan-digest comparison gets wrong.

        A note rides into the plan, so the digest moves while the geometry does
        not. Treating that as progress means running an identical build again.
        """

        def renote(spec: DesignSpec, diagnosis: Diagnosis) -> DesignSpec:
            return spec.with_features(
                [
                    FeatureSpec(f.name, f.op, f.args, when=f.when, note="rewritten")
                    for f in spec.features
                ]
            )

        report = correct(
            bracket(), build=build, repair=renote, assertions=BUDGET, max_attempts=9
        )

        assert report.stop is Stop.NO_PROGRESS
        # The count is the point. Comparing plan digests instead would let the
        # renoted spec through, build an identical part a second time, and only
        # notice on the round after — one wasted build, every time.
        assert len(report) == 1

    def test_oscillating_between_two_designs_is_caught_as_a_cycle(self) -> None:
        state = {"n": 0}

        def oscillate(spec: DesignSpec, diagnosis: Diagnosis) -> DesignSpec:
            state["n"] += 1
            return spec.set_parameter("thick_mm", 7.0 if state["n"] % 2 else 8.0)

        report = correct(
            bracket(), build=build, repair=oscillate, assertions=BUDGET, max_attempts=9
        )

        assert report.stop is Stop.CYCLE
        assert len(report) < 9, "it must not run to the cap"

    def test_a_repairer_that_declines_ends_it_cleanly(self) -> None:
        report = correct(bracket(), build=build, repair=lambda s, d: None, assertions=BUDGET)

        assert report.stop is Stop.DECLINED
        assert not report

    def test_the_attempt_cap_is_the_backstop(self) -> None:
        """When each repair is real but too small to converge."""
        report = correct(
            bracket(), build=build, repair=thin_by(0.1), assertions=BUDGET, max_attempts=3
        )

        assert report.stop is Stop.EXHAUSTED
        assert len(report) == 3

    def test_a_cap_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            correct(bracket(), build=build, repair=thin_by(1.0), max_attempts=0)


class TestTheDiagnosisIsActionable:
    def test_an_assertion_failure_carries_the_gap_not_just_the_verdict(self) -> None:
        seen: list[Diagnosis] = []

        def watch(spec: DesignSpec, diagnosis: Diagnosis) -> None:
            seen.append(diagnosis)
            return None

        correct(bracket(), build=build, repair=watch, assertions=BUDGET)

        assert len(seen) == 1
        diagnosis = seen[0]
        assert diagnosis.stage == "assertion"
        assert "mass budget" in diagnosis.details[0]
        assert "FAILED by" in diagnosis.details[0]

    def test_the_repairer_is_told_which_parameters_it_may_change(self) -> None:
        """A derived parameter cannot be set; offering one produces a refused repair."""
        seen: list[Diagnosis] = []

        def watch(spec: DesignSpec, diagnosis: Diagnosis) -> None:
            seen.append(diagnosis)
            return None

        correct(bracket(), build=build, repair=watch, assertions=BUDGET)

        diagnosis = seen[0]
        assert "thick_mm" in diagnosis.settable
        assert "width_mm" in diagnosis.settable
        assert "fillet_mm" not in diagnosis.settable, "it is derived from thick_mm"
        assert diagnosis.derived["fillet_mm"] == "thick_mm / 2"

    def test_the_diagnosis_reads_as_a_brief(self) -> None:
        seen: list[Diagnosis] = []

        def watch(spec: DesignSpec, diagnosis: Diagnosis) -> None:
            seen.append(diagnosis)
            return None

        correct(bracket(), build=build, repair=watch, assertions=BUDGET)

        text = str(seen[0])
        assert "Parameters that can be changed" in text
        assert "Derived, so change what they read instead" in text

    def test_a_build_failure_names_the_feature_and_the_stage(self) -> None:
        seen: list[Diagnosis] = []

        def watch(spec: DesignSpec, diagnosis: Diagnosis) -> None:
            seen.append(diagnosis)
            return None

        correct(bracket(), build=failing_build, repair=watch, assertions=BUDGET)

        diagnosis = seen[0]
        assert diagnosis.stage == "build"
        assert diagnosis.feature == "plate.body"
        assert "not closed" in diagnosis.summary

    def test_a_repair_that_does_not_compile_is_fed_back_not_crashed_on(self) -> None:
        """The path an LLM repairer takes most often, and the most useful one.

        The compiler's message already names the feature and says what to do, so
        it becomes the brief for the next attempt rather than an exception.
        """
        stages: list[str] = []

        def break_then_fix(spec: DesignSpec, diagnosis: Diagnosis) -> DesignSpec:
            stages.append(diagnosis.stage)
            if len(stages) == 1:
                return spec.with_features(
                    [*spec.features, FeatureSpec("oops", "catia_not_a_real_operation", {})]
                )
            # Drop the bad feature again and make a real repair.
            return spec.with_features(
                [f for f in spec.features if f.name != "oops"]
            ).set_parameter("thick_mm", 4.0)

        report = correct(
            bracket(),
            build=build,
            repair=break_then_fix,
            assertions=BUDGET,
            max_attempts=5,
        )

        assert stages == ["assertion", "compile"]
        assert report.stop is Stop.SATISFIED
        assert "not a real operation" not in report.summary()


class TestTheReport:
    def test_every_attempt_is_recorded_in_order(self) -> None:
        report = correct(
            bracket(), build=build, repair=thin_by(2.0), assertions=BUDGET, max_attempts=5
        )

        assert [a.number for a in report] == [1, 2, 3]
        assert not report.attempts[0].ok
        assert report.attempts[-1].ok

    def test_it_serialises(self) -> None:
        report = correct(
            bracket(), build=build, repair=thin_by(2.0), assertions=BUDGET, max_attempts=5
        )
        data = report.to_dict()

        assert data["stop"] == "satisfied"
        assert data["ok"] is True
        assert len(data["attempts"]) == 3
        assert "diagnosis" in data["attempts"][0]

    def test_a_custom_measurer_is_used_instead_of_the_last_result(self) -> None:
        """An assertion needing something the mutating call did not report."""
        wanted = [Assertion("wall", "min_wall_mm", ">=", 3.0)]

        def measure(report: BuildReport) -> Mapping[str, Any]:
            return {"min_wall_mm": 5.0}

        result = correct(
            bracket(), build=build, repair=lambda s, d: None, assertions=wanted, measure=measure
        )

        assert result.stop is Stop.SATISFIED

    def test_without_that_measurer_the_same_assertion_is_unmeasured(self) -> None:
        """And unmeasured is not a pass, so the loop tries to repair it."""
        wanted = [Assertion("wall", "min_wall_mm", ">=", 3.0)]

        result = correct(bracket(), build=build, repair=lambda s, d: None, assertions=wanted)

        assert result.stop is Stop.DECLINED
        assert not result
