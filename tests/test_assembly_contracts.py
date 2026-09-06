"""Master plan 14.2/14.3/14.4 — the interface contract, and what it names.

The property that makes a contract worth having is not that it fails. An assertion on a
part already fails. It is that **the failure names the other side**: change the
swingarm, and the answer is "the pivot contract is violated and the frame is the
counterparty", which is a sentence somebody acts on. Everything in this file is aimed at
that, plus the three ways it could quietly stop being true:

* a claim that could not be measured reported as satisfied
  (`TestAnUnmeasuredInterfaceIsNotASatisfiedOne`);
* an approximated number from one side reported as measured once it has been namespaced
  (`TestNamespacingCarriesProvenance` — the guard is verified by building the payload
  the way it would look without the re-keying, and asserting that version lies);
* two sides that agree today because they were typed twice
  (`TestBothSidesCompileAgainstTheContract`).

Offline and fast: no OCCT, no database. One test imports `app.kernel.provenance`
deliberately, to check that this package's local spelling of the sidecar key still
matches the kernel's.
"""

from __future__ import annotations

import pytest

from app.assembly.contracts import (
    CONSUMER,
    PROVENANCE_KEY,
    PROVIDER,
    Interface,
    affected,
    bind_both,
    bind_into,
    check,
    check_all,
    measurements,
    namespace,
)
from app.assembly.errors import ContractError
from app.assembly.structure import Component, Instance, ProductStructure
from app.design.assertions import Assertion, Outcome
from app.design.params import Parameter, ParameterSet, Unit
from app.design.spec import DesignSpec, FeatureSpec

# -- the contract under test: a swingarm pivot between a frame and a swingarm --


def _pivot_contract() -> Interface:
    return Interface(
        name="pivot_fit",
        provider="frame",
        consumer="swingarm",
        parameters=ParameterSet.of(
            [
                Parameter(name="pivot_diameter_mm", unit=Unit.MM, value=25.0),
                Parameter(name="pivot_centres_mm", unit=Unit.MM, value=180.0),
                Parameter(
                    name="pivot_bore_min_mm",
                    unit=Unit.MM,
                    expression="pivot_diameter_mm + 0.05",
                ),
            ]
        ),
        claims=(
            Assertion(
                name="bore_clears_the_pin",
                measure="consumer.bore_diameter_mm",
                comparison=">=",
                bound="=pivot_bore_min_mm",
                note="a bore below the pin diameter cannot be assembled at all",
            ),
            Assertion(
                name="lugs_are_at_the_contracted_spacing",
                measure="provider.lug_spacing_mm",
                comparison="==",
                bound="=pivot_centres_mm",
                tolerance=0.2,
            ),
        ),
        note="the swingarm pivot: the frame offers the lugs, the swingarm needs the bore",
    )


def _payload(**values: float) -> dict:
    return dict(values)


class TestAViolationNamesBothParties:
    def test_a_failed_claim_carries_the_provider_and_the_consumer(self) -> None:
        result = check(
            _pivot_contract(),
            measurements(
                provider=_payload(lug_spacing_mm=180.0),
                consumer=_payload(bore_diameter_mm=24.9),
            ),
        )

        assert not result.ok
        (violation,) = result.failed
        assert violation.claim == "bore_clears_the_pin"
        assert violation.provider == "frame"
        assert violation.consumer == "swingarm"
        assert violation.counterparty("swingarm") == "frame"
        assert violation.counterparty("frame") == "swingarm"
        assert "frame no longer meets swingarm" in str(violation)
        # The gap is what a correction loop needs: 24.9 against a 25.05 minimum.
        assert violation.result.gap == pytest.approx(-0.15, abs=1e-9)

    def test_a_satisfied_contract_is_truthy_and_lists_no_violations(self) -> None:
        result = check(
            _pivot_contract(),
            measurements(
                provider=_payload(lug_spacing_mm=180.1),
                consumer=_payload(bore_diameter_mm=25.1),
            ),
        )
        assert result.ok
        assert result.violations == ()
        assert bool(result) is True

    def test_asking_a_violation_about_a_third_party_is_refused(self) -> None:
        result = check(
            _pivot_contract(),
            measurements(consumer=_payload(bore_diameter_mm=24.0)),
        )
        with pytest.raises(ContractError, match="is neither"):
            result.violations[0].counterparty("shock")


class TestAnUnmeasuredInterfaceIsNotASatisfiedOne:
    def test_a_missing_measurement_is_unmeasured_and_not_passed(self) -> None:
        result = check(_pivot_contract(), {})

        assert not result.ok
        assert len(result.unmeasured) == 2
        assert result.failed == ()
        for violation in result.unmeasured:
            assert violation.outcome is Outcome.UNMEASURED
            assert "NOT CHECKED between frame and swingarm" in str(violation)

    def test_an_interface_with_no_payload_is_checked_not_skipped(self) -> None:
        """A contract nobody measured must not vanish from the report.

        The failure this pins is the one `app.dynamics.clearance` already had at pose
        level: work that was never done, counted as done. `check_all` given payloads for
        one of two interfaces returns two results, and the second is entirely unmeasured.
        """
        other = Interface(
            name="shock_mount",
            provider="frame",
            consumer="shock",
            claims=(
                Assertion(name="eye_clears", measure="minimum_clearance_mm", comparison=">=", bound=2.0),
            ),
        )
        results = check_all(
            [_pivot_contract(), other],
            {
                "pivot_fit": measurements(
                    provider=_payload(lug_spacing_mm=180.0),
                    consumer=_payload(bore_diameter_mm=25.5),
                )
            },
        )

        assert len(results) == 2
        assert results[0].ok
        assert not results[1].ok
        assert len(results[1].unmeasured) == 1

    def test_a_bound_reading_an_undeclared_parameter_is_unmeasured_with_a_reason(
        self,
    ) -> None:
        interface = Interface(
            name="mass_budget",
            provider="frame",
            consumer="swingarm",
            claims=(
                Assertion(
                    name="within_budget",
                    measure="consumer.mass_kg",
                    comparison="<=",
                    bound="=rear_mass_budget_kg",
                ),
            ),
        )
        result = check(interface, measurements(consumer=_payload(mass_kg=3.0)))

        assert len(result.unmeasured) == 1
        assert "rear_mass_budget_kg" in result.unmeasured[0].result.reason


class TestNamespacingCarriesProvenance:
    def test_the_local_provenance_key_matches_the_kernels(self) -> None:
        """Spelled locally to avoid importing OCP; pinned here so a rename cannot orphan it."""
        from app.kernel.provenance import PROVENANCE_KEY as KERNEL_KEY

        assert PROVENANCE_KEY == KERNEL_KEY

    def test_an_approximated_number_stays_approximated_after_namespacing(self) -> None:
        provider = {
            "minimum_wall_mm": 2.8,
            PROVENANCE_KEY: {
                "minimum_wall_mm": {
                    "basis": "approximated",
                    "method": "ray cast from 16 points per face",
                }
            },
        }
        merged = measurements(provider=provider)

        assert merged["provider"]["minimum_wall_mm"] == 2.8
        assert merged[PROVENANCE_KEY] == {
            "provider.minimum_wall_mm": {
                "basis": "approximated",
                "method": "ray cast from 16 points per face",
            }
        }

        interface = Interface(
            name="wall_contract",
            provider="cover",
            consumer="housing",
            claims=(
                Assertion(
                    name="wall_is_thick_enough",
                    measure="provider.minimum_wall_mm",
                    comparison=">=",
                    bound=2.5,
                ),
            ),
        )
        result = check(interface, merged)

        assert result.ok
        assert result.report.approximate is True
        assert result.report.results[0].approximate is True

    def test_without_the_re_keying_the_same_claim_reports_as_measured(self) -> None:
        """The guard, broken.

        This is what the payload looks like if `namespace` prefixes the body and leaves
        the sidecar keyed on the bare path — the obvious implementation. The claim still
        passes, and it passes *silently as an exact measurement*: `approximate` is False
        on a ray-cast bound. That is the mock-mass lie one layer up, and it is invisible
        in every other field of the result, which is why the guard exists.
        """
        from app.design.assertions import check_assertions

        broken = {
            "provider": {"minimum_wall_mm": 2.8},
            PROVENANCE_KEY: {
                "minimum_wall_mm": {"basis": "approximated", "method": "ray cast"}
            },
        }
        claim = Assertion(
            name="wall_is_thick_enough",
            measure="provider.minimum_wall_mm",
            comparison=">=",
            bound=2.5,
        )

        report = check_assertions([claim], broken)
        assert report.ok
        assert report.approximate is False  # the lie the re-keying removes

    def test_namespace_with_no_prefix_is_a_plain_copy(self) -> None:
        """The `boundary=` case, alone: nothing is prefixed and nothing is re-keyed."""
        payload = {"minimum_clearance_mm": 1.8, PROVENANCE_KEY: {"minimum_clearance_mm": {"basis": "measured"}}}
        copied = namespace(payload, "")

        assert copied == payload
        assert copied is not payload

    def test_the_boundary_payload_is_not_namespaced(self) -> None:
        """A clearance measured *between* the two sides keeps the kernel's own spelling."""
        merged = measurements(
            provider=_payload(mass_kg=4.0), boundary=_payload(minimum_clearance_mm=1.8)
        )
        assert merged["minimum_clearance_mm"] == 1.8
        assert merged["provider"]["mass_kg"] == 4.0

    def test_two_payloads_claiming_the_same_top_level_key_are_refused(self) -> None:
        with pytest.raises(ContractError, match="Namespacing exists"):
            measurements(boundary={"provider": {"mass_kg": 1.0}}, provider={"mass_kg": 2.0})


class TestBothSidesCompileAgainstTheContract:
    def _side(self, name: str, *parameters: Parameter) -> DesignSpec:
        return DesignSpec.of(
            name,
            parameters=parameters,
            features=[FeatureSpec(name=f"{name}.body", op="catia_pad", args={"length_mm": 10.0})],
        )

    def test_the_contracts_parameters_land_in_the_spec(self) -> None:
        spec = bind_into(_pivot_contract(), self._side("swingarm"))

        assert "pivot_diameter_mm" in spec.parameters.names()
        assert spec.parameters.resolve().number("pivot_bore_min_mm") == pytest.approx(25.05)
        # Contract parameters come first, so a reader sees what came from outside.
        assert spec.parameters.names()[0] == "pivot_diameter_mm"

    def test_both_sides_get_the_same_numbers_from_one_call(self) -> None:
        provider, consumer = bind_both(
            _pivot_contract(), self._side("frame"), self._side("swingarm")
        )
        assert provider.parameters.resolve().number(
            "pivot_centres_mm"
        ) == consumer.parameters.resolve().number("pivot_centres_mm")

    def test_a_side_that_redeclares_the_number_differently_is_refused_by_name(self) -> None:
        """14.3's compile error at the interface, naming both parties.

        The guard, broken: this is exactly the design that ships today without a
        contract — the swingarm was drawn to a 24 mm pin because somebody typed the
        number twice. Without this refusal both specs compile, both parts build, and the
        finding arrives as a clash three weeks later.
        """
        divergent = self._side(
            "swingarm", Parameter(name="pivot_diameter_mm", unit=Unit.MM, value=24.0)
        )
        with pytest.raises(ContractError) as caught:
            bind_into(_pivot_contract(), divergent)

        message = str(caught.value)
        assert "pivot_fit" in message
        assert "frame" in message and "swingarm" in message
        assert "25.0" in message and "24.0" in message

    def test_a_side_that_redeclares_the_number_identically_is_allowed(self) -> None:
        """The same declaration written twice is redundant, not a conflict."""
        same = self._side(
            "swingarm", Parameter(name="pivot_diameter_mm", unit=Unit.MM, value=25.0)
        )
        spec = bind_into(_pivot_contract(), same)
        assert spec.parameters.resolve().number("pivot_diameter_mm") == 25.0

    def test_a_side_that_redeclares_the_number_in_a_different_unit_is_refused(self) -> None:
        wrong_unit = self._side(
            "swingarm", Parameter(name="pivot_diameter_mm", unit=Unit.NONE, value=25.0)
        )
        with pytest.raises(ContractError, match="pivot_fit"):
            bind_into(_pivot_contract(), wrong_unit)

    def test_the_sides_own_parameters_survive_the_bind(self) -> None:
        spec = bind_into(
            _pivot_contract(),
            self._side("swingarm", Parameter(name="arm_length_mm", unit=Unit.MM, value=520.0)),
        )
        assert spec.parameters.resolve().number("arm_length_mm") == 520.0
        assert spec.name == "swingarm"
        assert spec.feature_names() == ("swingarm.body",)


class TestChangePropagation:
    def _interfaces(self) -> tuple[Interface, ...]:
        return (
            _pivot_contract(),
            Interface(
                name="shock_mount",
                provider="frame",
                consumer="shock",
                claims=(
                    Assertion(name="eye_clears", measure="minimum_clearance_mm", comparison=">=", bound=2.0),
                ),
            ),
            Interface(
                name="wheel_fit",
                provider="swingarm",
                consumer="wheel",
                claims=(
                    Assertion(name="axle_fits", measure="consumer.axle_mm", comparison="<=", bound=25.0),
                ),
            ),
        )

    def test_changing_one_component_names_every_counterparty(self) -> None:
        impacts = affected(self._interfaces(), ["swingarm"])

        assert [(i.interface, i.role, i.counterparty) for i in impacts] == [
            ("pivot_fit", CONSUMER, "frame"),
            ("wheel_fit", PROVIDER, "wheel"),
        ]
        assert "swingarm is the consumer; frame must be re-checked" in str(impacts[0])

    def test_an_unrelated_change_reaches_nothing(self) -> None:
        assert affected(self._interfaces(), ["seat"]) == ()

    def test_a_change_inside_a_sub_assembly_reaches_a_contract_on_the_sub_assembly(
        self,
    ) -> None:
        """A contract written against an occurrence path, and a change three levels in.

        Without the structure the party `bike/rear.1` and the changed component
        `bearing` have nothing in common as strings, so the contract would be reported
        as unaffected — and the bearing change would reach the frame with nobody told.
        """
        structure = ProductStructure(
            root="bike",
            components=[
                Component(name="bike", instances=(Instance(component="rear", tag="rear"),)),
                Component(
                    name="rear",
                    instances=(
                        Instance(component="swingarm", tag="swingarm"),
                        Instance(component="bearing", tag="bearing"),
                    ),
                ),
                Component(name="swingarm"),
                Component(name="bearing"),
            ],
        )
        interface = Interface(
            name="rear_end_envelope",
            provider="bike/rear.1",
            consumer="frame",
            claims=(
                Assertion(name="fits_the_envelope", measure="provider.bounding_box_mm.size[0]", comparison="<=", bound=700.0),
            ),
        )

        assert affected([interface], ["bearing"]) == ()
        with_graph = affected([interface], ["bearing"], structure=structure)
        assert len(with_graph) == 1
        assert with_graph[0].counterparty == "frame"
        assert with_graph[0].changed == "bike/rear.1"

    def test_party_matching_is_exact_and_never_a_substring(self) -> None:
        """`swingarm_pin` must not match a contract written against `swingarm`."""
        assert affected(self._interfaces(), ["swingarm_pin"]) == ()

    def test_a_path_resolves_to_components_and_not_to_the_tags_it_is_spelled_with(
        self,
    ) -> None:
        """A tag is not a component name, and a change-impact list must know it.

        `bike/rear.1` is the path of an instance *tagged* `rear` whose component is
        `rear_suspension`. There is also a real, unrelated component called `rear` bolted
        to the frame. An implementation that matched the changed name against the path's
        text — or against its tag segments, which is the same mistake spelled more
        carefully — reports the seat-`rear` change as reaching the swingarm envelope. A
        change-impact list with false entries stops being read, which is worse than a
        short one.
        """
        structure = ProductStructure(
            root="bike",
            components=[
                Component(
                    name="bike",
                    instances=(
                        Instance(component="rear_suspension", tag="rear", index=1),
                        Instance(component="rear", tag="mudguard", index=1),
                    ),
                ),
                Component(name="rear_suspension"),
                Component(name="rear"),
            ],
        )
        interface = Interface(
            name="rear_end_envelope",
            provider="bike/rear.1",
            consumer="frame",
            claims=(
                Assertion(
                    name="fits",
                    measure="provider.bounding_box_mm.size[0]",
                    comparison="<=",
                    bound=700.0,
                ),
            ),
        )

        # The mudguard changed. It is not what `bike/rear.1` names.
        assert affected([interface], ["rear"], structure=structure) == ()
        # What `bike/rear.1` does name still reaches it.
        assert len(affected([interface], ["rear_suspension"], structure=structure)) == 1

    def test_each_interface_appears_once_even_when_both_sides_changed(self) -> None:
        impacts = affected([_pivot_contract()], ["frame", "swingarm"])
        assert len(impacts) == 2
        assert {i.role for i in impacts} == {PROVIDER, CONSUMER}


class TestTheContractRefusesWhatCannotBeAContract:
    def test_one_party_named_twice_is_refused(self) -> None:
        with pytest.raises(ContractError, match="both provider and consumer"):
            Interface(
                name="self_fit",
                provider="frame",
                consumer="frame",
                claims=(Assertion(name="x", measure="mass_kg", comparison="<=", bound=1.0),),
            )

    def test_a_contract_that_checks_nothing_and_defines_nothing_is_refused(self) -> None:
        with pytest.raises(ContractError, match="checks\nnothing|checks nothing"):
            Interface(name="empty", provider="frame", consumer="swingarm")

    def test_an_empty_party_is_refused(self) -> None:
        with pytest.raises(ContractError, match="the consumer is empty"):
            Interface(
                name="dangling",
                provider="frame",
                consumer="   ",
                claims=(Assertion(name="x", measure="mass_kg", comparison="<=", bound=1.0),),
            )

    def test_a_nameless_interface_is_refused(self) -> None:
        with pytest.raises(ContractError, match="needs a name"):
            Interface(
                name="",
                provider="frame",
                consumer="swingarm",
                claims=(Assertion(name="x", measure="mass_kg", comparison="<=", bound=1.0),),
            )

    def test_a_contract_of_parameters_alone_is_allowed(self) -> None:
        """A contract that only *defines* is legitimate — both sides still import it."""
        interface = Interface(
            name="datum",
            provider="frame",
            consumer="swingarm",
            parameters=ParameterSet.of([Parameter(name="datum_z_mm", unit=Unit.MM, value=0.0)]),
        )
        assert check(interface, {}).ok is False  # no claims → an empty report is not ok
        assert interface.counterparty("frame") == "swingarm"


class TestSerialisation:
    def test_an_interface_round_trips_its_readable_form(self) -> None:
        data = _pivot_contract().to_dict()
        assert data["provider"] == "frame"
        assert data["consumer"] == "swingarm"
        assert [c["name"] for c in data["claims"]] == [
            "bore_clears_the_pin",
            "lugs_are_at_the_contracted_spacing",
        ]
        assert {p["name"] for p in data["parameters"]} == {
            "pivot_diameter_mm",
            "pivot_centres_mm",
            "pivot_bore_min_mm",
        }

    def test_a_result_serialises_with_both_parties_on_every_violation(self) -> None:
        result = check(
            _pivot_contract(),
            measurements(
                provider=_payload(lug_spacing_mm=200.0),
                consumer=_payload(bore_diameter_mm=25.5),
            ),
        )
        payload = result.to_dict()
        assert payload["ok"] is False
        assert payload["violations"][0]["provider"] == "frame"
        assert payload["violations"][0]["consumer"] == "swingarm"
        assert payload["violations"][0]["interface"] == "pivot_fit"
