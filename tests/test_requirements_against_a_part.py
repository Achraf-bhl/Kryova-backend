"""A requirement set checked against a part that OCCT actually built — Phase 11.

The four `tests/test_requirements_*.py` files check the package against literal
payloads, which is right: nothing in `app/requirements/` measures anything, and
keeping it that way is what lets a requirements document be read and validated
on a machine with no kernel. But it left the phase in the state this codebase
keeps rediscovering — green on capability, reached by nothing. `verify_requirements`
had never been handed a payload a kernel produced.

So this file closes the loop at the only place it can be closed: build the part
with `app.kernel.OcctRunner`, measure it through the runner's own operations, and
verify the set against what came back. Every number below is known before the
kernel is asked — a 60 x 40 x 20 pad in 1018 steel is 48,000 mm3 and 0.37776 kg —
so a wrong answer is a wrong answer and not a changed recording.

What it is really pinning, in order of how much it matters:

1. **The loop runs at all**, in the few lines the master plan implies it should be.
2. **A requirement nobody could check is reported and counts against coverage.**
   Two of them here, and they fail in the two different ways that exist: one has
   nothing in this build that measures it (a service life), and one names a real
   measurement that this payload does not carry because the scan producing it was
   never run. Both are UNMEASURED, neither is omitted, and both damage coverage.
3. **Running the scan turns the second one into an answer**, which is what makes
   the first case a gap in the *run* rather than a gap in the system —
   `RequirementSet.scans_needed()` is what tells a caller which scan that was.
4. **The verdicts are `app.design.assertions`'** — there is no second `Outcome`
   in this codebase and no report here that can be truthy while something in it
   was never checked.

Runs offline; skipped where OCCT is not installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.design.assertions import Outcome
from app.kernel.occt import available
from app.requirements import (
    Requirement,
    RequirementSet,
    Source,
    Status,
    verify_requirements,
)
from app.requirements.verification import NOT_ATTEMPTED

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

#: The part: a 60 x 40 x 20 pad in 1018 steel. Known before the kernel is asked.
WIDTH, HEIGHT, THICKNESS = 60.0, 40.0, 20.0
VOLUME_MM3 = WIDTH * HEIGHT * THICKNESS
DENSITY_KG_M3 = 7870.0
MASS_KG = VOLUME_MM3 * 1e-9 * DENSITY_KG_M3  # 0.37776 kg


def build_plate() -> Any:
    """The part, through `OcctRunner` — the same seam a CATIA seat executes."""
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "Plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner(
        "catia_sketch_rectangle",
        {"sketch": "outline", "width_mm": WIDTH, "height_mm": HEIGHT},
    )
    runner("catia_pad", {"sketch": "outline", "length_mm": THICKNESS})
    runner("catia_set_material", {"material": "steel-1018"})
    return runner


def measured(runner: Any, *scans: str) -> dict[str, Any]:
    """The measurement payload, plus any named analyses, merged into one mapping.

    This is the whole glue between a built part and a requirement set, and it is
    deliberately short: `InterrogationPayload.merge` exists precisely so the
    provenance sidecars survive the merge, which a `dict.update` would flatten.
    """
    from app.kernel.interrogation import InterrogationPayload

    payload = InterrogationPayload().merge(dict(runner("catia_measure", {})))
    for kind in scans:
        payload.merge(dict(runner("catia_analysis_part", {"kind": kind})))
    return payload.as_dict()


def plate_requirements() -> RequirementSet:
    """Four requirements: one met, one violated, two that cannot be checked.

    Deliberately not all measurable. A set where everything can be measured
    cannot demonstrate the property the phase exists for.
    """
    return RequirementSet.of(
        "plate",
        [
            Requirement(
                id="REQ-001",
                statement="The plate shall weigh no more than 0.5 kg.",
                measure="mass_kg",
                comparison="<=",
                target=0.5,
                source=Source.CUSTOMER,
                rationale="One person lifts it into the fixture.",
            ),
            Requirement(
                id="REQ-002",
                statement="The plate shall stand at least 25 mm proud of the table.",
                measure="bounding_box_mm.size[2]",
                comparison=">=",
                target=25.0,
                source=Source.CUSTOMER,
            ),
            Requirement(
                id="REQ-003",
                statement="No wall shall be thinner than 6 mm.",
                measure="minimum_wall_mm",
                comparison=">=",
                target=6.0,
                source=Source.STANDARD,
                citation="House casting rule CR-3",
            ),
            Requirement(
                id="REQ-004",
                statement="The plate shall survive ten years of two-shift operation.",
                needs="no fatigue solver is federated yet (Phase 6)",
                source=Source.CUSTOMER,
            ),
        ],
    )


@pytest.fixture(scope="module")
def runner() -> Any:
    return build_plate()


@pytest.fixture(scope="module")
def measurement(runner: Any) -> dict[str, Any]:
    """What the part reports with no interrogation run — the default a caller gets."""
    return measured(runner)


# -- the loop ----------------------------------------------------------------


class TestTheLoopRuns:
    """Build, measure, verify. If this cannot be written, the phase is not connected."""

    def test_the_kernel_measures_the_part_we_think_it_built(
        self, measurement: dict[str, Any]
    ) -> None:
        """Closed form first: a wrong payload would make every verdict below noise."""
        assert measurement["volume_mm3"] == pytest.approx(VOLUME_MM3)
        assert measurement["mass_kg"] == pytest.approx(MASS_KG)
        assert measurement["bounding_box_mm"]["size"] == pytest.approx(
            [WIDTH, HEIGHT, THICKNESS], abs=1e-6
        )

    def test_a_requirement_set_verifies_against_it(
        self, measurement: dict[str, Any]
    ) -> None:
        report = verify_requirements(
            plate_requirements(), measurement, bound_to={"backend": "occt"}
        )

        assert len(report) == 4
        assert report.result_for("REQ-001").outcome is Outcome.PASSED
        assert report.result_for("REQ-001").measured == pytest.approx(MASS_KG)

    def test_the_verdicts_are_the_assertion_layers_own(
        self, measurement: dict[str, Any]
    ) -> None:
        """No second verdict type: `Outcome` here is `app.design.assertions.Outcome`."""
        from app.design import assertions

        report = verify_requirements(plate_requirements(), measurement)
        assert all(one.outcome in set(assertions.Outcome) for one in report)
        assert Outcome is assertions.Outcome

    def test_a_violated_requirement_reports_the_gap_in_the_measurements_unit(
        self, measurement: dict[str, Any]
    ) -> None:
        report = verify_requirements(plate_requirements(), measurement)
        result = report.result_for("REQ-002")

        assert result.outcome is Outcome.FAILED
        assert result.measured == pytest.approx(THICKNESS, abs=1e-6)
        assert abs(result.gap or 0.0) == pytest.approx(5.0, abs=1e-6)
        assert result.requirement.unit == "mm"

    def test_the_report_is_bound_to_what_produced_it(
        self, runner: Any, measurement: dict[str, Any]
    ) -> None:
        """Decision 3, with a real backend version rather than a placeholder."""
        report = verify_requirements(
            plate_requirements(),
            measurement,
            bound_to={"backend": "occt", "kernel": runner.backend_version()},
        )

        assert report.traceable
        assert report.bound_to["kernel"].startswith("OCCT")
        assert report.contract_version

    def test_no_number_is_claimed_exact_that_nobody_recorded_as_exact(
        self, measurement: dict[str, Any]
    ) -> None:
        """Evidence is read from the payload, never inferred from the verdict.

        Worth knowing while reading this: `metrology.measure` attaches **no**
        provenance sidecar, so an exactly integrated OCCT mass arrives here as
        `unrecorded` and `Coverage.by_measurement` is 0 against a real part. That
        is the honest reading of a payload that made no claim, and it is a gap in
        the kernel rather than here. The invariant asserted is the one that must
        hold either way: nothing counts as exact evidence unless the payload said
        so, so this test keeps passing when the sidecar is added.
        """
        report = verify_requirements(plate_requirements(), measurement)

        for one in report:
            if one.evidence.exact:
                assert one.evidence.basis == "measured"
        assert report.coverage.by_measurement == sum(
            1 for one in report if one.verified and one.evidence.exact
        )


# -- what could not be checked -----------------------------------------------


class TestNothingIsSilentlyOmitted:
    """The failure the phase exists to refuse, against a part that really exists."""

    def test_both_kinds_of_unverifiable_requirement_appear(
        self, measurement: dict[str, Any]
    ) -> None:
        report = verify_requirements(plate_requirements(), measurement)

        assert {one.id for one in report.unverified} == {"REQ-003", "REQ-004"}

    def test_a_requirement_with_no_measurement_says_what_would_change_that(
        self, measurement: dict[str, Any]
    ) -> None:
        result = verify_requirements(plate_requirements(), measurement).result_for(
            "REQ-004"
        )

        assert result.outcome is Outcome.UNMEASURED
        assert "fatigue solver" in result.reason
        assert result.evidence.basis == NOT_ATTEMPTED

    def test_a_scan_that_was_never_run_is_unmeasured_and_says_so(
        self, measurement: dict[str, Any]
    ) -> None:
        """`measure()` never interrogates. A missing scan must not read as a pass."""
        result = verify_requirements(plate_requirements(), measurement).result_for(
            "REQ-003"
        )

        assert result.outcome is Outcome.UNMEASURED
        assert "minimum_wall_mm" in result.reason
        assert result.measured is None

    def test_neither_is_a_pass_and_the_report_is_not_ok(
        self, measurement: dict[str, Any]
    ) -> None:
        report = verify_requirements(plate_requirements(), measurement)

        assert not report.ok
        assert not bool(report)
        assert {one.id for one in report.met} == {"REQ-001"}

    def test_they_count_against_coverage(self, measurement: dict[str, Any]) -> None:
        coverage = verify_requirements(plate_requirements(), measurement).coverage

        assert coverage.total == 4
        assert coverage.verified == 2  # one met, one violated
        assert coverage.unverified == 2
        assert coverage.fraction == pytest.approx(0.5)

    def test_the_summary_names_them_rather_than_reporting_a_percentage(
        self, measurement: dict[str, Any]
    ) -> None:
        summary = verify_requirements(plate_requirements(), measurement).summary()

        assert "REQ-003 NOT VERIFIED" in summary
        assert "REQ-004 NOT VERIFIED" in summary
        assert "passed" not in summary.lower()

    def test_a_retired_requirement_is_excluded_and_listed(
        self, measurement: dict[str, Any]
    ) -> None:
        """Retiring one must not look like losing coverage, or like deleting it."""
        original = plate_requirements()
        retired = original.with_requirements(
            [
                one
                if one.id != "REQ-004"
                else Requirement(**{**one.to_dict(), "status": Status.OBSOLETE})
                for one in original
            ]
        )

        report = verify_requirements(retired, measurement)
        assert [one.id for one in report.obsolete] == ["REQ-004"]
        assert report.coverage.total == 3
        assert "REQ-004" not in {one.id for one in report.results}


# -- running the scan the set asks for ---------------------------------------


class TestRunningTheScanTheSetAsksFor:
    """A missing measurement is a gap in the run, and the set can say which scan."""

    def test_the_set_names_the_analysis_its_paths_need(self) -> None:
        assert plate_requirements().scans_needed() == ("thickness",)

    def test_a_set_needing_nothing_extra_asks_for_nothing(self) -> None:
        mass_only = RequirementSet.of(
            "mass only",
            [
                Requirement(
                    id="REQ-001",
                    statement="Under half a kilo.",
                    measure="mass_kg",
                    comparison="<=",
                    target=0.5,
                )
            ],
        )
        assert mass_only.scans_needed() == ()

    def test_running_it_turns_the_unmeasured_requirement_into_an_answer(
        self, runner: Any
    ) -> None:
        requirements = plate_requirements()
        payload = measured(runner, *requirements.scans_needed())

        result = verify_requirements(requirements, payload).result_for("REQ-003")
        assert result.outcome is Outcome.PASSED
        # A 20 mm solid plate: the thinnest wall is its own thickness.
        assert result.measured == pytest.approx(THICKNESS, abs=1e-6)

    def test_coverage_rises_by_exactly_that_one_requirement(
        self, runner: Any, measurement: dict[str, Any]
    ) -> None:
        requirements = plate_requirements()
        before = verify_requirements(requirements, measurement).coverage
        after = verify_requirements(
            requirements, measured(runner, *requirements.scans_needed())
        ).coverage

        assert (before.verified, after.verified) == (2, 3)
        assert after.fraction == pytest.approx(0.75)
        # And the one that never could be checked is still not checked.
        assert after.unverified == 1

    def test_the_scanned_number_is_reported_as_approximated_not_as_measured(
        self, runner: Any
    ) -> None:
        """A ray cast is an upper bound. Coverage says so rather than smoothing it."""
        requirements = plate_requirements()
        report = verify_requirements(
            requirements, measured(runner, *requirements.scans_needed())
        )
        evidence = report.result_for("REQ-003").evidence

        assert evidence.approximate
        assert not evidence.exact
        assert "ray cast" in evidence.method
        assert report.coverage.by_approximation == 1

    def test_every_kind_the_table_names_is_one_the_registry_offers(self) -> None:
        """The table is kept in step with the operation by a test, not by discipline."""
        from app.catia.ops import OPERATIONS
        from app.requirements.vocabulary import ANALYSIS_KINDS

        operation = next(
            one for one in OPERATIONS if one.name == "catia_analysis_part"
        )
        allowed = set(operation.json_schema()["properties"]["kind"]["enum"])

        assert set(ANALYSIS_KINDS.values()) <= allowed

    def test_every_path_the_table_names_is_produced_by_the_kind_it_names(
        self, runner: Any
    ) -> None:
        """Against the real kernel, because the table was wrong once by reasoning.

        `open_edge_count` reads like a validity result and is reported under
        curvature. The only way that stays right is by asking the part.
        """
        from app.kernel.provenance import PROVENANCE_KEY
        from app.requirements.vocabulary import ANALYSIS_KINDS

        emitted: dict[str, set[str]] = {}
        for kind in sorted(set(ANALYSIS_KINDS.values())):
            payload = dict(runner("catia_analysis_part", {"kind": kind}))
            emitted[kind] = {key for key in payload if key != PROVENANCE_KEY}

        for path, kind in ANALYSIS_KINDS.items():
            produced = {one for one in emitted.values() for one in one}
            if path not in produced:
                # Not every path is defined on every part — a box has no concave
                # face, so curvature reports no minimum concave radius. What must
                # never happen is a path being produced by a *different* kind
                # than the table names.
                continue
            assert path in emitted[kind], f"{path} is not produced by {kind!r}"


# -- master plan 5.2: a requirement's shortfall aims the repair ---------------
#
# The last thing standing between a requirement and the self-correcting loop.
# `Requirement.assertion()` already makes a failing report say "REQ-002 not met"
# rather than "size[2] >= 25 failed" — which is the readable half of 5.2 — and
# what this pins is the useful half: that the same result carries a `gap` the
# sensitivity layer can turn into *which parameter to move and how far*, without
# `app.design` knowing that requirements exist or `app.requirements` knowing that
# geometry does.


def parametric_plate() -> Any:
    """The same plate as a `DesignSpec`, so its thickness is a free parameter."""
    from app.design.params import Parameter, ParameterSet, Unit
    from app.design.spec import DesignSpec, FeatureSpec, expr, ref

    return DesignSpec(
        name="plate",
        parameters=ParameterSet.of(
            [
                Parameter(name="width_mm", value=WIDTH, unit=Unit.MM),
                Parameter(name="height_mm", value=HEIGHT, unit=Unit.MM),
                Parameter(name="thickness_mm", value=THICKNESS, unit=Unit.MM),
            ]
        ),
        features=(
            FeatureSpec("plate.outline", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "plate.profile",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("plate.outline"),
                    "width_mm": expr("width_mm"),
                    "height_mm": expr("height_mm"),
                },
            ),
            FeatureSpec(
                "plate.slab",
                "catia_pad",
                {"sketch": ref("plate.outline"), "length_mm": expr("thickness_mm")},
                note="Extrude the footprint to thickness — the parameter a repair moves.",
            ),
            FeatureSpec("plate.material", "catia_set_material", {"material": "steel-1018"}),
        ),
    )


def occt_probe(spec: Any) -> dict[str, Any]:
    """Build a spec on the open kernel and hand back what it measures.

    The probe `sensitivity` injects. Decision 1 in one function: this is a few
    milliseconds headless, and it was minutes of a licensed workstation.
    """
    from app.design.compile import compile_spec
    from app.design.execute import execute_plan
    from app.kernel import OcctRunner

    runner = OcctRunner()
    report = execute_plan(compile_spec(spec), runner)
    assert report.ok, report.failure
    return dict(runner("catia_measure", {}))


class TestARequirementCanAimItsOwnRepair:
    def test_the_gap_on_a_violated_requirement_names_a_parameter_and_a_distance(
        self,
    ) -> None:
        from app.design.sensitivity import aim, baseline_values, sensitivity

        spec = parametric_plate()
        wanted_kg = 0.30  # the 20 mm plate is 0.37776 kg, so this is violated
        requirement = Requirement(
            id="REQ-014",
            statement="The plate shall weigh no more than 0.30 kg.",
            measure="mass_kg",
            comparison="<=",
            target=wanted_kg,
            source=Source.CUSTOMER,
            rationale="One person lifts it into the fixture, repeatedly.",
        )
        payload = occt_probe(spec)
        report = verify_requirements(RequirementSet.of("plate", [requirement]), payload)

        result = report.result_for("REQ-014")
        assert result.outcome is Outcome.FAILED
        assert "REQ-014 NOT MET" in str(result)
        assert result.gap is not None and result.gap > 0.0

        influence = sensitivity(spec, "mass_kg", probe=occt_probe)
        # **Which** parameter is named here, and why it is named rather than
        # discovered: a rectangular plate's mass is exactly as elastic in width as
        # in height as in thickness — double any one and it doubles — so
        # `most_influential` is genuinely a tie and picking a winner from it would
        # be arbitrary dressed as analysis. Choosing the thickness is an
        # engineering decision (the footprint is what bolts to the fixture), and
        # `aim` takes it as an argument for exactly this case.
        suggestion = aim(
            influence,
            result.gap,
            values=baseline_values(spec),
            parameter="thickness_mm",
        )
        assert suggestion is not None
        assert suggestion.parameter == "thickness_mm"
        # Mass is linear in thickness, so first order is exact here: the step that
        # lands on 0.30 kg is thickness * 0.30/0.37776 = 15.882 mm.
        assert suggestion.to_value == pytest.approx(
            THICKNESS * wanted_kg / MASS_KG, rel=1e-3
        )
        assert suggestion.change < 0.0
        assert suggestion.caveat

    def test_moving_the_parameter_it_names_makes_the_requirement_met(self) -> None:
        """The loop closed: requirement -> gap -> parameter -> rebuild -> met.

        Nothing in `app/design/` imports `app/requirements/` to do this, and
        nothing in `app/requirements/` imports a kernel. The two meet at the gap,
        which is a number.

        **The requirement carries 1 g of tolerance and it has to.** A first-order
        step aimed at a hard bound lands *on* the bound, and on a bound with no
        slack the verdict is then decided by the last bit of the arithmetic — this
        exact case rebuilds to 0.30000000000000093 kg against a target of 0.30 and
        is correctly refused. That is not a defect in either layer: it is what
        aiming at an inequality means, and the two honest answers are to give the
        requirement the slack a real one has or to aim inside the bound. A
        correction loop that "fixed" it by loosening the comparison would be
        deciding an engineering question in a retry counter.
        """
        from app.design.sensitivity import aim, baseline_values, sensitivity

        spec = parametric_plate()
        requirement = Requirement(
            id="REQ-014",
            statement="The plate shall weigh no more than 0.30 kg.",
            measure="mass_kg",
            comparison="<=",
            target=0.30,
            tolerance=0.001,  # 1 g — see the docstring
            source=Source.CUSTOMER,
        )
        given = RequirementSet.of("plate", [requirement])
        failing = verify_requirements(given, occt_probe(spec)).result_for("REQ-014")
        assert failing.gap is not None

        influence = sensitivity(spec, "mass_kg", probe=occt_probe)
        suggestion = aim(
            influence,
            failing.gap,
            values=baseline_values(spec),
            parameter="thickness_mm",
        )
        assert suggestion is not None

        assert suggestion.to_value is not None
        repaired = with_thickness(spec, suggestion.to_value)
        after = verify_requirements(given, occt_probe(repaired)).result_for("REQ-014")
        assert after.outcome is Outcome.PASSED
        assert after.measured == pytest.approx(0.30, rel=1e-9)


def with_thickness(spec: Any, thickness_mm: float) -> Any:
    """The same spec with one parameter moved — an edit is a rebuild, not a mutation."""
    from app.design.params import Parameter, ParameterSet, Unit

    return type(spec)(
        name=spec.name,
        parameters=ParameterSet.of(
            [
                Parameter(name=one.name, value=one.value, unit=one.unit)
                if one.name != "thickness_mm"
                else Parameter(name=one.name, value=thickness_mm, unit=Unit.MM)
                for one in spec.parameters
            ]
        ),
        features=spec.features,
    )
