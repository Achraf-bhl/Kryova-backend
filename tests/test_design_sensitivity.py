"""Phase 5.3 — which parameter moves this measurement most.

Offline, like the rest of `app/design/`: the probe is a closure over a formula,
so a "build" is arithmetic and the whole file runs in milliseconds. That is the
point of the injected `Probe` — the module under test must not know whether a
kernel, a CATIA seat or this function answered.

The tests that matter are the ones about **not** producing a number: a build
that fails is not zero sensitivity, a topology change is not a derivative, and a
derived parameter is excluded with a reason rather than dropped. Getting any of
those wrong yields a plausible ranking that aims a repair at the wrong thing,
which is worse than the retry counter 5.3 exists to replace.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.design.errors import SpecError
from app.design.params import Parameter, ParameterSet, Unit
from app.design.sensitivity import (
    RELATIVE_STEP,
    aim,
    baseline_values,
    sensitivity,
    topology_of,
)
from app.design.spec import DesignSpec


def _spec(**values: float) -> DesignSpec:
    return DesignSpec(
        name="plate",
        parameters=ParameterSet.of(
            Parameter(name=name, value=value, unit=Unit.MM) for name, value in values.items()
        ),
    )


def _value(spec: DesignSpec, name: str) -> float:
    for one in spec.parameters:
        if one.name == name:
            assert one.value is not None
            return float(one.value)
    raise KeyError(name)


def _mass_probe(formula: Any, *, faces: int | None = None) -> Any:
    """A 'build' that is arithmetic, optionally reporting a face count."""

    def probe(spec: DesignSpec) -> dict[str, Any]:
        payload: dict[str, Any] = {"mass_kg": formula(spec)}
        if faces is not None:
            payload["face_count"] = faces
        return payload

    return probe


class TestTheDerivativeIsRight:
    def test_a_linear_measurement_gives_its_own_slope(self) -> None:
        """mass = 3*length + 7  ⇒  d(mass)/d(length) = 3, exactly."""
        spec = _spec(length_mm=10.0)
        found = sensitivity(
            spec, "mass_kg", probe=_mass_probe(lambda s: 3.0 * _value(s, "length_mm") + 7.0)
        )
        assert found["length_mm"].derivative == pytest.approx(3.0, rel=1e-6)

    def test_central_differencing_is_exact_on_a_quadratic(self) -> None:
        """A central difference has no first-order error; a forward one would."""
        spec = _spec(length_mm=4.0)
        found = sensitivity(spec, "mass_kg", probe=_mass_probe(lambda s: _value(s, "length_mm") ** 2))
        assert found["length_mm"].derivative == pytest.approx(8.0, rel=1e-6)
        assert found["length_mm"].scheme == "central"

    def test_the_step_is_relative_to_the_parameters_own_magnitude(self) -> None:
        """A step that suits a 4 mm fillet is noise on a 4000 mm frame."""
        big = sensitivity(
            _spec(length_mm=4000.0), "mass_kg", probe=_mass_probe(lambda s: _value(s, "length_mm"))
        )
        assert big["length_mm"].step == pytest.approx(4000.0 * RELATIVE_STEP)

    def test_a_zero_valued_parameter_still_gets_a_real_step(self) -> None:
        """A relative step of a zero parameter is zero, and would divide by it."""
        found = sensitivity(
            _spec(offset_mm=0.0), "mass_kg", probe=_mass_probe(lambda s: 2.0 * _value(s, "offset_mm"))
        )
        assert found["offset_mm"].step > 0
        assert found["offset_mm"].derivative == pytest.approx(2.0, rel=1e-6)

    def test_a_non_positive_step_is_refused(self) -> None:
        with pytest.raises(SpecError, match="not a step"):
            sensitivity(_spec(a_mm=1.0), "mass_kg", probe=_mass_probe(lambda s: 1.0), relative_step=0.0)


class TestAFailedBuildIsNotZeroSensitivity:
    """Reporting 0.0 tells a correction loop to leave alone the one thing at its limit."""

    def test_a_parameter_that_builds_in_neither_direction_is_unprobed(self) -> None:
        def probe(spec: DesignSpec) -> dict[str, Any]:
            if _value(spec, "radius_mm") != 5.0:
                raise RuntimeError("the fillet radius exceeds the adjacent face")
            return {"mass_kg": 1.0}

        found = sensitivity(_spec(radius_mm=5.0), "mass_kg", probe=probe)
        influence = found["radius_mm"]
        assert influence.derivative is None
        assert influence.measured is False
        assert "exceeds the adjacent face" in influence.reason

    def test_a_one_sided_probe_falls_back_and_says_which_side(self) -> None:
        """Half an answer, honestly labelled, beats no answer and beats a fake one."""

        def probe(spec: DesignSpec) -> dict[str, Any]:
            radius = _value(spec, "radius_mm")
            if radius > 5.0:
                raise RuntimeError("too large")
            return {"mass_kg": 2.0 * radius}

        found = sensitivity(_spec(radius_mm=5.0), "mass_kg", probe=probe)
        influence = found["radius_mm"]
        assert influence.scheme == "backward"
        assert influence.derivative == pytest.approx(2.0, rel=1e-3)

    def test_an_unprobed_parameter_ranks_last_not_first(self) -> None:
        def probe(spec: DesignSpec) -> dict[str, Any]:
            if _value(spec, "bad_mm") != 1.0:
                raise RuntimeError("no")
            return {"mass_kg": 5.0 * _value(spec, "good_mm")}

        found = sensitivity(_spec(bad_mm=1.0, good_mm=2.0), "mass_kg", probe=probe)
        assert found.ranked()[0].parameter == "good_mm"
        assert found.ranked()[-1].parameter == "bad_mm"

    def test_a_missing_measurement_on_the_baseline_probes_nothing(self) -> None:
        found = sensitivity(_spec(a_mm=1.0), "mass_kg", probe=lambda spec: {"volume_mm3": 1.0})
        assert found.baseline is None
        assert found.influences == ()
        assert "could not be measured" in found.summary()


class TestATopologyChangeIsNotADerivative:
    def test_a_changed_face_count_refuses_the_influence(self) -> None:
        """A fillet that swallows a face makes the two builds different parts."""

        def probe(spec: DesignSpec) -> dict[str, Any]:
            radius = _value(spec, "radius_mm")
            return {"mass_kg": 2.0 * radius, "face_count": 6 if radius <= 5.0 else 5}

        found = sensitivity(_spec(radius_mm=5.0), "mass_kg", probe=probe)
        influence = found["radius_mm"]
        assert influence.topology_changed is True
        assert influence.derivative is None
        assert "different parts" in influence.reason
        assert "smaller relative_step" in influence.reason

    def test_an_unchanged_face_count_leaves_the_influence_alone(self) -> None:
        found = sensitivity(
            _spec(length_mm=10.0),
            "mass_kg",
            probe=_mass_probe(lambda s: 3.0 * _value(s, "length_mm"), faces=6),
        )
        assert found["length_mm"].derivative == pytest.approx(3.0, rel=1e-6)
        assert found["length_mm"].topology_unchecked is False

    def test_a_payload_with_no_counts_is_unchecked_not_assumed_unchanged(self) -> None:
        """Claiming a check that never ran is the failure mode this avoids."""
        found = sensitivity(
            _spec(length_mm=10.0), "mass_kg", probe=_mass_probe(lambda s: 3.0 * _value(s, "length_mm"))
        )
        assert found["length_mm"].topology_unchecked is True

    def test_topology_of_reads_only_what_is_there(self) -> None:
        assert topology_of({"face_count": 6}) == {"face_count": 6}
        assert topology_of({"mass_kg": 1.0}) == {}


class TestOnlyDecisionsAreProbed:
    def test_a_derived_parameter_is_excluded_with_a_reason_not_dropped(self) -> None:
        """Absent from a ranking reads as 'no influence', which is a different claim."""
        spec = DesignSpec(
            name="plate",
            parameters=ParameterSet.of(
                [
                    Parameter(name="width_mm", value=40.0, unit=Unit.MM),
                    Parameter(name="half_width_mm", expression="width_mm / 2", unit=Unit.MM),
                ]
            ),
        )
        found = sensitivity(spec, "mass_kg", probe=_mass_probe(lambda s: 3.0 * _value(s, "width_mm")))
        derived = found["half_width_mm"]
        assert derived.measured is False
        assert "consequence" in derived.reason
        assert "width_mm / 2" in derived.reason

    def test_a_named_subset_can_be_probed(self) -> None:
        spec = _spec(a_mm=1.0, b_mm=2.0, c_mm=3.0)
        found = sensitivity(
            spec, "mass_kg", probe=_mass_probe(lambda s: 1.0), parameters=["b_mm"]
        )
        assert [one.parameter for one in found.influences] == ["b_mm"]

    def test_asking_for_a_parameter_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(SpecError, match="no such parameter"):
            sensitivity(
                _spec(a_mm=1.0), "mass_kg", probe=_mass_probe(lambda s: 1.0), parameters=["b_mm"]
            )

    def test_the_callers_spec_is_not_mutated_by_the_sweep(self) -> None:
        """A sweep that edited in place would leave the last perturbation behind."""
        spec = _spec(length_mm=10.0)
        sensitivity(spec, "mass_kg", probe=_mass_probe(lambda s: 3.0 * _value(s, "length_mm")))
        assert _value(spec, "length_mm") == 10.0


class TestTheRankingIsComparable:
    def test_it_ranks_on_elasticity_not_on_the_raw_derivative(self) -> None:
        """kg/mm and kg/degree cannot be compared; percentages can."""
        spec = _spec(length_mm=1000.0, angle_deg=5.0)
        # A tiny slope over a huge parameter beats a big slope over a tiny one.
        probe = _mass_probe(
            lambda s: 0.01 * _value(s, "length_mm") + 0.5 * _value(s, "angle_deg")
        )
        found = sensitivity(spec, "mass_kg", probe=probe)
        assert abs(found["angle_deg"].derivative) > abs(found["length_mm"].derivative)
        assert found.most_influential().parameter == "length_mm"

    def test_a_zero_baseline_leaves_elasticity_undefined_rather_than_infinite(self) -> None:
        found = sensitivity(_spec(a_mm=2.0), "mass_kg", probe=_mass_probe(lambda s: 0.0))
        assert found["a_mm"].elasticity is None

    def test_ties_break_on_declaration_order_so_a_ranking_is_stable(self) -> None:
        spec = _spec(first_mm=10.0, second_mm=10.0)
        probe = _mass_probe(lambda s: _value(s, "first_mm") + _value(s, "second_mm"))
        assert [one.parameter for one in sensitivity(spec, "mass_kg", probe=probe).ranked()] == [
            "first_mm",
            "second_mm",
        ]

    def test_asking_for_an_unrecorded_parameter_lists_what_was_probed(self) -> None:
        found = sensitivity(_spec(a_mm=1.0), "mass_kg", probe=_mass_probe(lambda s: 1.0))
        with pytest.raises(KeyError, match="a_mm"):
            found["nonexistent_mm"]


class TestAimingARepair:
    def _found(self) -> Any:
        spec = _spec(length_mm=10.0, width_mm=2.0)
        probe = _mass_probe(lambda s: 3.0 * _value(s, "length_mm") + 0.1 * _value(s, "width_mm"))
        return spec, sensitivity(spec, "mass_kg", probe=probe)

    def test_it_names_the_parameter_and_the_distance(self) -> None:
        spec, found = self._found()
        suggestion = aim(found, gap=3.0, values=baseline_values(spec))
        assert suggestion is not None
        assert suggestion.parameter == "length_mm"
        # 3.0 kg over, at 3 kg per mm ⇒ 1 mm smaller.
        assert suggestion.change == pytest.approx(-1.0, rel=1e-3)
        assert suggestion.to_value == pytest.approx(9.0, rel=1e-3)

    def test_every_suggestion_carries_its_first_order_caveat(self) -> None:
        spec, found = self._found()
        suggestion = aim(found, gap=3.0, values=baseline_values(spec))
        assert suggestion is not None
        assert "First-order" in suggestion.caveat
        assert "rebuild and re-measure" in suggestion.caveat

    def test_it_refuses_rather_than_dividing_by_a_negligible_derivative(self) -> None:
        """A step the size of a planet costs the loop an attempt on an impossible build."""
        found = sensitivity(_spec(a_mm=5.0), "mass_kg", probe=_mass_probe(lambda s: 7.0))
        assert aim(found, gap=3.0) is None

    def test_it_returns_nothing_when_nothing_could_be_probed(self) -> None:
        def probe(spec: DesignSpec) -> dict[str, Any]:
            if _value(spec, "a_mm") != 1.0:
                raise RuntimeError("no")
            return {"mass_kg": 1.0}

        assert aim(sensitivity(_spec(a_mm=1.0), "mass_kg", probe=probe), gap=1.0) is None

    def test_without_values_it_reports_the_change_and_invents_no_baseline(self) -> None:
        """'0 → -1.4' reads as an instruction and would be a fabrication."""
        _, found = self._found()
        suggestion = aim(found, gap=3.0)
        assert suggestion is not None
        assert suggestion.from_value is None
        assert "→" not in str(suggestion)

    def test_a_one_sided_derivative_widens_the_caveat(self) -> None:
        def probe(spec: DesignSpec) -> dict[str, Any]:
            radius = _value(spec, "radius_mm")
            if radius > 5.0:
                raise RuntimeError("too large")
            return {"mass_kg": 2.0 * radius}

        suggestion = aim(sensitivity(_spec(radius_mm=5.0), "mass_kg", probe=probe), gap=1.0)
        assert suggestion is not None
        assert "backward" in suggestion.caveat

    def test_a_specific_parameter_can_be_aimed_at(self) -> None:
        spec, found = self._found()
        suggestion = aim(found, gap=3.0, values=baseline_values(spec), parameter="width_mm")
        assert suggestion is not None
        assert suggestion.parameter == "width_mm"

    def test_baseline_values_reports_decisions_only(self) -> None:
        spec = DesignSpec(
            name="plate",
            parameters=ParameterSet.of(
                [
                    Parameter(name="width_mm", value=40.0, unit=Unit.MM),
                    Parameter(name="half_mm", expression="width_mm / 2", unit=Unit.MM),
                ]
            ),
        )
        assert baseline_values(spec) == {"width_mm": 40.0}
