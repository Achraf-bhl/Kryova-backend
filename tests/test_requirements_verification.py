"""Phase 11.2 and 11.4 — verification against a built part, and coverage.

The rule under nearly every test here is the codebase's own: **an unmeasured
claim is never a pass, and it is never omitted.** A requirements report that
quietly leaves out what it could not check is the exact failure mode this
package exists to refuse, so several of these tests exist only to prove that
something *appears* in the report.

Offline: the measurement payloads are literals, because nothing in this package
measures anything.
"""

from __future__ import annotations

import pytest

from app.design.params import Parameter, ParameterSet, Unit
from app.kernel import provenance
from app.requirements.errors import RequirementError
from app.requirements.model import Outcome, Requirement, RequirementSet, Source, Status
from app.requirements.verification import (
    NOT_ATTEMPTED,
    UNRECORDED,
    verify_requirements,
)


def mass(**overrides: object) -> Requirement:
    fields: dict[str, object] = {
        "id": "REQ-001",
        "statement": "The frame shall weigh no more than 850 kg.",
        "measure": "mass_kg",
        "comparison": "<=",
        "target": 850.0,
        "source": Source.CUSTOMER,
    }
    fields.update(overrides)
    return Requirement(**fields)  # type: ignore[arg-type]


def wall(**overrides: object) -> Requirement:
    fields: dict[str, object] = {
        "id": "REQ-002",
        "statement": "No wall thinner than 6 mm.",
        "measure": "minimum_wall_mm",
        "comparison": ">=",
        "target": 6.0,
    }
    fields.update(overrides)
    return Requirement(**fields)  # type: ignore[arg-type]


def life() -> Requirement:
    return Requirement(
        id="REQ-003",
        statement="The frame shall survive ten years of two-shift operation.",
        needs="no fatigue solver is federated yet (Phase 6)",
        source=Source.CUSTOMER,
    )


class TestTheThreeOutcomes:
    def test_a_met_requirement_passes(self) -> None:
        report = verify_requirements(RequirementSet.of("press", [mass()]), {"mass_kg": 800.0})
        assert report.result_for("REQ-001").outcome is Outcome.PASSED
        assert report.ok

    def test_a_violated_requirement_fails_and_reports_the_gap(self) -> None:
        report = verify_requirements(RequirementSet.of("press", [mass()]), {"mass_kg": 900.0})
        result = report.result_for("REQ-001")
        assert result.outcome is Outcome.FAILED
        assert result.gap == pytest.approx(50.0)
        assert not report.ok
        assert "out by 50" in str(result)

    def test_a_missing_measurement_is_unmeasured_and_never_a_pass(self) -> None:
        report = verify_requirements(RequirementSet.of("press", [mass()]), {})
        result = report.result_for("REQ-001")
        assert result.outcome is Outcome.UNMEASURED
        assert not result.met
        assert not report.ok
        assert "mass_kg" in result.reason

    def test_an_unavailable_measurement_quotes_the_reason_the_backend_gave(self) -> None:
        payload: dict[str, object] = {}
        provenance.attach(
            payload,
            "mass_kg",
            provenance.unavailable("no density has been set on this part"),
        )
        report = verify_requirements(RequirementSet.of("press", [mass()]), payload)
        assert "no density has been set" in report.result_for("REQ-001").reason


class TestNothingIsEverOmitted:
    def test_a_requirement_with_nothing_to_measure_still_appears(self) -> None:
        """Broken to verify: made `verify_requirements` iterate the assertions
        rather than the set. The report then had one row, said 1/1 verified, and
        the ten-year service life had vanished from a document that claimed to
        cover it."""
        report = verify_requirements(
            RequirementSet.of("press", [mass(), life()]), {"mass_kg": 800.0}
        )
        assert [one.id for one in report.results] == ["REQ-001", "REQ-003"]
        unverifiable = report.result_for("REQ-003")
        assert unverifiable.outcome is Outcome.UNMEASURED
        assert "fatigue solver" in unverifiable.reason
        assert not report.ok

    def test_it_counts_as_uncovered(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass(), life()]), {"mass_kg": 800.0}
        )
        assert report.coverage.total == 2
        assert report.coverage.verified == 1
        assert report.coverage.unverified == 1
        assert report.coverage.fraction == pytest.approx(0.5)

    def test_an_obsolete_requirement_is_excluded_but_listed(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass(), wall(status=Status.OBSOLETE)]),
            {"mass_kg": 800.0},
        )
        assert [one.id for one in report.results] == ["REQ-001"]
        assert [one.id for one in report.obsolete] == ["REQ-002"]
        assert "retired and not counted" in report.summary()

    def test_the_summary_never_says_the_requirements_passed(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass(), life()]), {"mass_kg": 800.0}
        )
        summary = report.summary()
        assert "1 met" in summary
        assert "1 not verifiable" in summary
        assert "REQ-003 NOT VERIFIED" in summary

    def test_asking_for_a_requirement_that_is_not_in_the_report_says_what_is(self) -> None:
        report = verify_requirements(RequirementSet.of("press", [mass()]), {"mass_kg": 1.0})
        with pytest.raises(RequirementError) as caught:
            report.result_for("REQ-404")
        assert "REQ-001" in str(caught.value)


class TestCoverageIsNotPassing:
    def test_a_violated_requirement_is_covered(self) -> None:
        """Verified is 'we know the answer', not 'the answer is yes'. Broken to
        verify: counted only PASSED as verified, and a set that was fully measured
        and entirely violated reported 0% coverage — which reads as 'nobody
        checked' and would send someone to run the checks again."""
        report = verify_requirements(RequirementSet.of("press", [mass()]), {"mass_kg": 900.0})
        assert report.coverage.fraction == pytest.approx(1.0)
        assert report.coverage.failed == 1
        assert not report.ok

    def test_an_empty_set_is_zero_percent_not_one_hundred(self) -> None:
        report = verify_requirements(RequirementSet.of("press", []), {})
        assert report.coverage.fraction == 0.0
        assert not report.ok
        assert "no active requirements" in report.summary()

    def test_coverage_counts_only_active_requirements(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass(), wall(status=Status.OBSOLETE)]),
            {"mass_kg": 800.0},
        )
        assert report.coverage.total == 1


class TestEvidenceIsReadNotInferred:
    def payload(self) -> dict[str, object]:
        found: dict[str, object] = {"mass_kg": 800.0, "minimum_wall_mm": 7.0}
        provenance.attach(
            found, "mass_kg", provenance.measured("BRepGProp volume integration")
        )
        provenance.attach(
            found,
            "minimum_wall_mm",
            provenance.approximated("ray cast from 64 points per face"),
        )
        return found

    def test_an_exact_number_and_a_sampled_one_are_told_apart(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass(), wall()]), self.payload()
        )
        assert report.result_for("REQ-001").evidence.exact
        assert report.result_for("REQ-002").evidence.approximate
        assert "ray cast" in report.result_for("REQ-002").evidence.method

    def test_coverage_reports_the_split(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass(), wall()]), self.payload()
        )
        assert report.coverage.by_measurement == 1
        assert report.coverage.by_approximation == 1
        assert "rest on an approximated measurement" in report.summary()

    def test_a_payload_with_no_sidecar_is_unrecorded_not_measured(self) -> None:
        """Broken to verify: fell back to `measured` when the sidecar was silent.
        A third-party payload then reported every requirement as verified by exact
        measurement — a claim nobody had made."""
        report = verify_requirements(RequirementSet.of("press", [mass()]), {"mass_kg": 800.0})
        evidence = report.result_for("REQ-001").evidence
        assert evidence.basis == UNRECORDED
        assert not evidence.exact
        assert not evidence.approximate

    def test_the_payload_wide_approximate_flag_is_still_honoured(self) -> None:
        """The CATIA mock sets one flag for the whole payload and no sidecar."""
        report = verify_requirements(
            RequirementSet.of("press", [mass()]), {"mass_kg": 800.0, "approximate": True}
        )
        assert report.result_for("REQ-001").evidence.approximate

    def test_a_requirement_with_nothing_to_measure_has_no_evidence(self) -> None:
        report = verify_requirements(RequirementSet.of("press", [life()]), {})
        evidence = report.result_for("REQ-003").evidence
        assert evidence.basis == NOT_ATTEMPTED
        assert str(evidence) == "no measurement attempted"

    def test_unverified_requirements_contribute_no_evidence_to_coverage(self) -> None:
        report = verify_requirements(RequirementSet.of("press", [mass(), life()]), {})
        assert report.coverage.by_measurement == 0
        assert report.coverage.by_approximation == 0


class TestBoundToWhatProducedIt:
    def test_a_binding_is_carried_and_the_contract_version_stamped(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass()]),
            {"mass_kg": 800.0},
            bound_to={"plan_digest": "abc123", "backend": "occt"},
        )
        assert report.traceable
        assert report.to_dict()["bound_to"]["backend"] == "occt"
        assert report.contract_version

    def test_an_unbound_report_says_so(self) -> None:
        report = verify_requirements(RequirementSet.of("press", [mass()]), {"mass_kg": 1.0})
        assert not report.traceable
        assert "cannot be traced back" in report.summary()


class TestFormulaTargets:
    def test_a_target_tracking_a_design_parameter_is_resolved(self) -> None:
        one = mass(target="=target_mass_kg")
        parameters = ParameterSet.of(
            [Parameter(name="target_mass_kg", unit=Unit.KG, value=850.0)]
        ).resolve()
        report = verify_requirements(
            RequirementSet.of("press", [one]), {"mass_kg": 800.0}, parameters=parameters
        )
        assert report.result_for("REQ-001").expected == pytest.approx(850.0)

    def test_without_the_parameters_it_is_unmeasured_not_a_pass(self) -> None:
        report = verify_requirements(
            RequirementSet.of("press", [mass(target="=target_mass_kg")]), {"mass_kg": 800.0}
        )
        result = report.result_for("REQ-001")
        assert result.outcome is Outcome.UNMEASURED
        assert "parameters" in result.reason


class TestAPlainIterableIsAccepted:
    def test_a_list_of_requirements_verifies_without_a_set(self) -> None:
        report = verify_requirements([mass()], {"mass_kg": 800.0})
        assert report.ok
        assert report.coverage.total == 1


# -- 11.1's second half: validation flows back up the decomposition -----------
#
# A top-level requirement names no measurement of its own — it was decomposed
# into derived ones that do. Verified requirement by requirement it comes back
# NOT VERIFIED for ever, so the one requirement the customer signed is the one
# the report is silent about while everything below it passes.


def decomposed(**overrides: object) -> Requirement:
    """The customer's top-level requirement: no measurement, met through others."""
    fields: dict[str, object] = {
        "id": "REQ-000",
        "statement": "The press shall be light enough for a pallet truck.",
        "source": Source.CUSTOMER,
        "rationale": "The customer moves it between two bays with a hand truck.",
    }
    fields.update(overrides)
    return Requirement(**fields)  # type: ignore[arg-type]


def under(parent: str, one: Requirement) -> Requirement:
    """The same requirement, decomposed from `parent`."""
    return Requirement(
        id=one.id,
        statement=one.statement,
        measure=one.measure,
        comparison=one.comparison,
        target=one.target,
        tolerance=one.tolerance,
        source=Source.DERIVED,
        rationale=one.rationale,
        parents=(parent,),
        needs=one.needs,
    )


class TestValidationFlowsUpTheDecomposition:
    def test_a_decomposed_requirement_is_met_when_its_children_are(self) -> None:
        given = RequirementSet.of(
            "press", [decomposed(), under("REQ-000", mass()), under("REQ-000", wall())]
        )
        report = verify_requirements(
            given, {"mass_kg": 800.0, "minimum_wall_mm": 8.0}
        )
        top = report.result_for("REQ-000")
        assert top.outcome is Outcome.PASSED
        assert top.derived_from == ("REQ-001", "REQ-002")
        assert report.ok

    def test_a_violated_child_fails_its_parent(self) -> None:
        """Even though the parent itself was never measured — the alternative is a
        report where the customer's requirement is silent and the derived one is red."""
        given = RequirementSet.of(
            "press", [decomposed(), under("REQ-000", mass()), under("REQ-000", wall())]
        )
        report = verify_requirements(
            given, {"mass_kg": 900.0, "minimum_wall_mm": 8.0}
        )
        top = report.result_for("REQ-000")
        assert top.outcome is Outcome.FAILED
        assert "REQ-001 is not met" in top.reason
        assert not report.ok

    def test_an_unverified_child_leaves_its_parent_unverified(self) -> None:
        given = RequirementSet.of(
            "press", [decomposed(), under("REQ-000", mass()), under("REQ-000", wall())]
        )
        report = verify_requirements(given, {"mass_kg": 800.0})
        top = report.result_for("REQ-000")
        assert top.outcome is Outcome.UNMEASURED
        assert "REQ-002 was never verified" in top.reason

    def test_a_derived_verdict_is_never_counted_as_measured(self) -> None:
        """It rests on the decomposition being complete, which nothing checks."""
        given = RequirementSet.of(
            "press", [decomposed(), under("REQ-000", mass()), under("REQ-000", wall())]
        )
        payload: dict[str, object] = {"mass_kg": 800.0, "minimum_wall_mm": 8.0}
        provenance.attach(
            payload, "mass_kg", provenance.measured("BRepGProp volume integration")
        )
        provenance.attach(
            payload,
            "minimum_wall_mm",
            provenance.approximated("ray cast from 64 points per face"),
        )
        report = verify_requirements(given, payload)
        coverage = report.coverage
        assert coverage.by_decomposition == 1
        assert coverage.by_measurement == 1
        assert coverage.by_approximation == 1
        assert report.result_for("REQ-000").evidence.derived
        assert not report.result_for("REQ-000").evidence.exact
        assert "decomposition is complete" in report.result_for("REQ-000").reason

    def test_the_caveat_is_printed_where_a_reader_meets_the_verdict(self) -> None:
        given = RequirementSet.of("press", [decomposed(), under("REQ-000", mass())])
        report = verify_requirements(given, {"mass_kg": 800.0})
        assert "not measured at all" in str(report.coverage)
        assert "met through REQ-001" in str(report.result_for("REQ-000"))

    def test_it_flows_up_more_than_one_level(self) -> None:
        """A grandparent resolves the round after its child, not never."""
        given = RequirementSet.of(
            "press",
            [
                decomposed(),
                Requirement(
                    id="REQ-100",
                    statement="The frame subassembly shall be light.",
                    source=Source.DERIVED,
                    parents=("REQ-000",),
                ),
                under("REQ-100", mass()),
                under("REQ-100", wall()),
            ],
        )
        report = verify_requirements(given, {"mass_kg": 800.0, "minimum_wall_mm": 8.0})
        assert report.result_for("REQ-100").outcome is Outcome.PASSED
        assert report.result_for("REQ-000").outcome is Outcome.PASSED
        assert report.result_for("REQ-000").derived_from == ("REQ-100",)
        assert report.coverage.by_decomposition == 2

    def test_a_requirement_waiting_on_a_capability_does_not_flow_up(self) -> None:
        """`needs` says nothing measures it; children are what a verdict comes from,
        and this one has none. It stays unverified with its own reason."""
        report = verify_requirements(RequirementSet.of("press", [life()]), {})
        result = report.result_for("REQ-003")
        assert result.outcome is Outcome.UNMEASURED
        assert result.derived_from == ()
        assert "fatigue solver" in result.reason

    def test_a_measured_parent_keeps_its_own_number(self) -> None:
        """A measurement of the thing itself outranks an inference about it."""
        given = RequirementSet.of(
            "press",
            [
                mass(id="REQ-000", target=850.0),
                under("REQ-000", wall()),
            ],
        )
        report = verify_requirements(
            given, {"mass_kg": 900.0, "minimum_wall_mm": 8.0}
        )
        top = report.result_for("REQ-000")
        assert top.outcome is Outcome.FAILED
        assert top.derived_from == ()
        assert top.measured == pytest.approx(900.0)

    def test_an_obsolete_child_is_not_asked(self) -> None:
        """It is retired, and a retired requirement holding up its parent for ever
        would make retiring one cost coverage."""
        given = RequirementSet.of(
            "press",
            [
                decomposed(),
                under("REQ-000", mass()),
                Requirement(
                    id="REQ-009",
                    statement="Old thickness rule.",
                    measure="minimum_wall_mm",
                    comparison=">=",
                    target=99.0,
                    source=Source.DERIVED,
                    parents=("REQ-000",),
                    status=Status.OBSOLETE,
                ),
            ],
        )
        report = verify_requirements(given, {"mass_kg": 800.0})
        assert report.result_for("REQ-000").outcome is Outcome.PASSED
        assert report.result_for("REQ-000").derived_from == ("REQ-001",)
