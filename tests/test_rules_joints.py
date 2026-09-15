"""A bolted joint's rules: clamp length, preload over service load, thread engagement (E13.1).

The bolt is a real catalogue record (`app.parts.fasteners.hex_bolt`), so the clamp range and
the preload are the codebase's own derivations, read back rather than retyped. Every ratio a
rule compares against is a test fixture with a source that says so.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.design.assertions import Outcome
from app.kernel.provenance import Basis, basis_of
from app.parts.fasteners import assembly_preload, clamp_length_range_mm, hex_bolt
from app.rules.errors import RuleError
from app.rules.joints import BoltedJoint, _payload, assertions, check_joint
from app.rules.processes import Limit

FIXTURE = "test fixture, not a design guide"
BOLT = hex_bolt("M8", 60.0, "8.8")
LOW, HIGH = clamp_length_range_mm(BOLT)
MIDDLE = (LOW + HIGH) / 2.0
PRELOAD = assembly_preload(BOLT, thread_friction=0.14, utilisation=0.9).value


def _joint(**overrides: Any) -> BoltedJoint:
    kwargs: dict[str, Any] = {
        "name": "flange",
        "bolt": BOLT,
        "stack_mm": MIDDLE,
        "service_load_n": 5000.0,
        "service_load_source": "test fixture load case",
        "minimum_preload_ratio": Limit(1.5, FIXTURE),
        "thread_friction": 0.14,
        "utilisation": 0.9,
    }
    kwargs.update(overrides)
    return BoltedJoint(**kwargs)


def _outcomes(joint: BoltedJoint) -> dict[str, Outcome]:
    return {r.name: r.outcome for r in check_joint(joint).results}


class TestTheBoltClampsTheStack:
    def test_a_stack_inside_the_catalogue_range_passes(self) -> None:
        assert _outcomes(_joint())["flange.clamps"] is Outcome.PASSED

    @pytest.mark.parametrize("stack", [LOW - 1.0, HIGH + 1.0])
    def test_a_stack_outside_either_end_fails(self, stack: float) -> None:
        assert _outcomes(_joint(stack_mm=stack))["flange.clamps"] is Outcome.FAILED

    def test_the_margin_is_the_distance_to_the_nearer_end(self) -> None:
        payload = _payload(_joint(stack_mm=LOW + 0.5))
        assert payload["joint"]["flange"]["clamp_margin_mm"] == pytest.approx(0.5)

    def test_the_margin_is_approximated_because_the_range_is_an_estimate(self) -> None:
        payload = _payload(_joint())
        assert basis_of(payload, "joint.flange.clamp_margin_mm") is Basis.APPROXIMATED


class TestThePreloadHoldsTheJointShut:
    def test_the_ratio_is_the_vdi_2230_preload_over_the_service_load(self) -> None:
        payload = _payload(_joint(service_load_n=4000.0))
        assert payload["joint"]["flange"]["preload_n"] == pytest.approx(PRELOAD)
        assert payload["joint"]["flange"]["preload_ratio"] == pytest.approx(PRELOAD / 4000.0)

    def test_a_load_the_preload_comfortably_exceeds_passes(self) -> None:
        joint = _joint(service_load_n=PRELOAD / 3.0)
        assert _outcomes(joint)["flange.preload"] is Outcome.PASSED

    def test_a_load_close_to_the_preload_fails_the_stated_ratio(self) -> None:
        joint = _joint(service_load_n=PRELOAD / 1.2)
        assert _outcomes(joint)["flange.preload"] is Outcome.FAILED

    def test_the_ratio_is_approximated_and_names_its_inputs(self) -> None:
        payload = _payload(_joint())
        assert basis_of(payload, "joint.flange.preload_ratio") is Basis.APPROXIMATED

    def test_a_different_friction_moves_the_preload(self) -> None:
        dry = _payload(_joint(thread_friction=0.2))["joint"]["flange"]["preload_n"]
        assert dry < PRELOAD

    def test_the_assertion_carries_the_ratio_source(self) -> None:
        preload = next(a for a in assertions(_joint()) if a.name == "flange.preload")
        assert FIXTURE in preload.note
        assert preload.bound == 1.5


class TestATappedHoleEngagesEnoughThread:
    def _tapped(self, engagement: float) -> BoltedJoint:
        return _joint(
            engagement_mm=engagement, minimum_engagement_ratio=Limit(1.0, FIXTURE)
        )

    def test_engagement_of_one_and_a_half_diameters_passes_a_ratio_of_one(self) -> None:
        assert _outcomes(self._tapped(12.0))["flange.engagement"] is Outcome.PASSED

    def test_half_a_diameter_fails(self) -> None:
        assert _outcomes(self._tapped(4.0))["flange.engagement"] is Outcome.FAILED

    def test_the_ratio_is_over_the_nominal_diameter(self) -> None:
        payload = _payload(self._tapped(12.0))
        assert payload["joint"]["flange"]["engagement_ratio"] == pytest.approx(1.5)

    def test_a_through_bolt_has_no_engagement_rule(self) -> None:
        names = [a.name for a in assertions(_joint())]
        assert names == ["flange.clamps", "flange.preload"]


class TestWhatAJointRefuses:
    def test_a_name_with_a_dot_because_it_is_a_measurement_path(self) -> None:
        with pytest.raises(RuleError, match="without dots"):
            _joint(name="flange.1")

    def test_a_blank_name(self) -> None:
        with pytest.raises(RuleError, match="without dots"):
            _joint(name=" ")

    @pytest.mark.parametrize("load", [0.0, -100.0])
    def test_a_service_load_that_does_not_open_the_joint(self, load: float) -> None:
        with pytest.raises(RuleError, match="positive"):
            _joint(service_load_n=load)

    def test_a_service_load_with_no_source(self) -> None:
        with pytest.raises(RuleError, match="where the service load came from"):
            _joint(service_load_source="")

    def test_a_stack_of_nothing(self) -> None:
        with pytest.raises(RuleError, match="clamps nothing"):
            _joint(stack_mm=0.0)

    def test_an_engaged_length_without_its_minimum(self) -> None:
        with pytest.raises(RuleError, match="needs both"):
            _joint(engagement_mm=12.0)

    def test_a_minimum_without_an_engaged_length(self) -> None:
        with pytest.raises(RuleError, match="needs both"):
            _joint(minimum_engagement_ratio=Limit(1.0, FIXTURE))


class TestTheReport:
    def test_a_sound_joint_is_ok_but_not_proven(self) -> None:
        report = check_joint(_joint(service_load_n=PRELOAD / 3.0))
        assert report.ok
        assert report.approximate
