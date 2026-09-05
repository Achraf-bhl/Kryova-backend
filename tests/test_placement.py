"""Polar placement — the vocabulary gap that made a bolt circle unsayable.

Measured on this seat 2026-09-05, driving the real chat endpoint with
`qwen3-coder:30b`: asked for a flange with four holes on a 70 mm bolt circle,
the model placed circles at (35, 35). That is a bolt circle of 99 mm. Every
call returned `ok`, the part built, and nothing in the transcript looked wrong.

The model was not the defect. `catia_sketch_circle` offered exactly one way to
say where a circle goes — Cartesian `at` — while the system prompt forbids the
model from doing coordinate arithmetic, on the grounds that coordinate
arithmetic belongs inside a tool where it can be tested. The tool therefore
required the one thing the prompt forbade.

These tests pin the fix at all three layers it has to hold at: the arithmetic,
the declaration the model reads, and the dispatcher that consumes it before
either backend sees a call.
"""

from __future__ import annotations

import math

import pytest

from app.catia.ops.placement import (
    POLAR_KEYS,
    PlacementError,
    declares_polar,
    polar_placement,
    resolve_polar,
)
from app.catia.tool_specs import get_spec

#: The part that was got wrong, in the words it was asked for: "four holes on a
#: 70 mm bolt circle, one in each corner direction".
BOLT_CIRCLE_RADIUS_MM = 35.0
CORNER_DEG = 45.0
#: 35 * cos(45°) — the number a model cannot be expected to type from a drawing.
CORNER_MM = BOLT_CIRCLE_RADIUS_MM * math.sqrt(0.5)

SKETCH_PRIMITIVES = [
    "catia_sketch_circle",
    "catia_sketch_rectangle",
    "catia_sketch_polygon",
]


class TestTheArithmeticIsDoneWhereItCanBeTested:
    def test_the_bolt_circle_that_was_got_wrong(self) -> None:
        out = resolve_polar(
            {
                "diameter_mm": 9.0,
                "at_radius_mm": BOLT_CIRCLE_RADIUS_MM,
                "at_angle_deg": CORNER_DEG,
            }
        )

        assert out["at"] == pytest.approx([CORNER_MM, CORNER_MM])
        assert out["at"][0] != pytest.approx(
            BOLT_CIRCLE_RADIUS_MM
        ), "using the radius as a coordinate is the failure being fixed"

    def test_the_four_positions_lie_on_the_circle_that_was_asked_for(self) -> None:
        for angle in (45.0, 135.0, 225.0, 315.0):
            at = resolve_polar(
                {"at_radius_mm": BOLT_CIRCLE_RADIUS_MM, "at_angle_deg": angle}
            )["at"]
            assert math.hypot(*at) == pytest.approx(BOLT_CIRCLE_RADIUS_MM)

    def test_zero_degrees_is_the_horizontal_axis(self) -> None:
        """The convention catia_sketch_arc already uses, and there must be one."""
        at = resolve_polar({"at_radius_mm": 10.0, "at_angle_deg": 0.0})["at"]

        assert at == pytest.approx([10.0, 0.0])

    def test_ninety_degrees_is_the_vertical_axis_anticlockwise(self) -> None:
        at = resolve_polar({"at_radius_mm": 10.0, "at_angle_deg": 90.0})["at"]

        assert at == pytest.approx([0.0, 10.0])

    def test_a_negative_angle_turns_the_other_way(self) -> None:
        at = resolve_polar({"at_radius_mm": 10.0, "at_angle_deg": -90.0})["at"]

        assert at == pytest.approx([0.0, -10.0])

    def test_the_angle_defaults_to_zero_when_only_a_radius_is_given(self) -> None:
        assert resolve_polar({"at_radius_mm": 7.5})["at"] == pytest.approx([7.5, 0.0])

    def test_a_radius_of_zero_is_the_origin_and_not_an_error(self) -> None:
        """A design sweeping a radius down to nothing must not fail at the end."""
        at = resolve_polar({"at_radius_mm": 0.0, "at_angle_deg": 30.0})["at"]

        assert at == pytest.approx([0.0, 0.0])


class TestThePolarKeysAreConsumedNotForwarded:
    def test_they_are_removed_from_the_resolved_arguments(self) -> None:
        out = resolve_polar(
            {"diameter_mm": 9.0, "at_radius_mm": 35.0, "at_angle_deg": 45.0}
        )

        assert not set(POLAR_KEYS) & set(out)

    def test_everything_else_is_carried_through_untouched(self) -> None:
        out = resolve_polar(
            {
                "diameter_mm": 9.0,
                "sketch": "@plate.profile",
                "construction": True,
                "at_radius_mm": 35.0,
                "at_angle_deg": 45.0,
            }
        )

        assert out["diameter_mm"] == 9.0
        assert out["sketch"] == "@plate.profile"
        assert out["construction"] is True

    def test_a_call_with_no_polar_key_is_returned_unchanged(self) -> None:
        """This runs over every call, so the no-op case is the common one."""
        arguments = {"diameter_mm": 9.0, "at": [10.0, 20.0]}

        assert resolve_polar(arguments) == arguments

    def test_a_cartesian_call_still_works_exactly_as_before(self) -> None:
        assert resolve_polar({"at": [10.0, 20.0]})["at"] == [10.0, 20.0]


class TestItRefusesTwoAnswersThatCanDisagree:
    def test_at_together_with_polar_is_refused(self) -> None:
        with pytest.raises(PlacementError, match="not both"):
            resolve_polar(
                {"at": [1.0, 2.0], "at_radius_mm": 35.0, "at_angle_deg": 45.0}
            )

    def test_an_angle_with_no_radius_is_refused_with_the_reason(self) -> None:
        with pytest.raises(PlacementError, match="direction, not a point"):
            resolve_polar({"at_angle_deg": 45.0})

    def test_a_negative_radius_is_refused_and_says_what_to_do(self) -> None:
        with pytest.raises(PlacementError, match="180 degrees"):
            resolve_polar({"at_radius_mm": -35.0, "at_angle_deg": 45.0})

    def test_a_radius_that_is_not_a_number_is_refused(self) -> None:
        with pytest.raises(PlacementError, match="at_radius_mm"):
            resolve_polar({"at_radius_mm": "thirty five"})

    def test_an_infinite_radius_is_refused_rather_than_producing_nan(self) -> None:
        with pytest.raises(PlacementError):
            resolve_polar({"at_radius_mm": float("inf"), "at_angle_deg": 45.0})

    def test_a_nan_angle_is_refused(self) -> None:
        with pytest.raises(PlacementError):
            resolve_polar({"at_radius_mm": 35.0, "at_angle_deg": float("nan")})


class TestTheModelIsActuallyOfferedIt:
    """A capability the model is not shown is a capability it does not have."""

    @pytest.mark.parametrize("tool", SKETCH_PRIMITIVES)
    def test_the_placement_arguments_are_in_the_model_facing_schema(
        self, tool: str
    ) -> None:
        spec = get_spec(tool)

        assert spec is not None
        for key in POLAR_KEYS:
            assert key in spec.parameters["properties"], f"{tool} does not offer {key}"

    @pytest.mark.parametrize("tool", SKETCH_PRIMITIVES)
    def test_neither_is_required_so_every_existing_call_stays_valid(
        self, tool: str
    ) -> None:
        spec = get_spec(tool)

        assert spec is not None
        assert not set(POLAR_KEYS) & set(spec.parameters.get("required", []))

    @pytest.mark.parametrize("tool", SKETCH_PRIMITIVES)
    def test_the_schema_would_have_refused_a_polar_call_before_this_change(
        self, tool: str
    ) -> None:
        """additionalProperties: false is what made this a hard block rather
        than a field the daemon quietly ignored."""
        spec = get_spec(tool)

        assert spec is not None
        assert spec.parameters["additionalProperties"] is False

    def test_the_daemon_never_sees_them(self) -> None:
        """They are resolved on the server, so the workstation needs no change —
        and, more to the point, there is one implementation of the angle."""
        from app.catia.ops.registry import OPERATIONS

        for operation in OPERATIONS:
            if operation.name != "catia_sketch_circle":
                continue
            assert not set(POLAR_KEYS) & set(operation.daemon_schema()["properties"])
            return
        pytest.fail("catia_sketch_circle is not in the registry")

    def test_the_description_tells_the_model_the_radius_is_not_the_coordinate(
        self,
    ) -> None:
        spec = get_spec("catia_sketch_circle")

        assert spec is not None
        text = spec.parameters["properties"]["at_radius_mm"]["description"]
        assert "bolt circle" in text

    def test_the_summary_points_at_polar_for_a_radial_position(self) -> None:
        """Stated as a rule about how a request is worded, not as a recipe for
        one part — a recipe only ever covers the case somebody thought of."""
        spec = get_spec("catia_sketch_circle")

        assert spec is not None
        assert "at_radius_mm" in spec.description
        assert "at_angle_deg" in spec.description


class TestDeclaresPolar:
    """Dispatch asks the schema, not a list of tool names, so adding
    polar_placement() to a new operation is the whole of the change."""

    def test_a_tool_that_offers_it_is_recognised(self) -> None:
        spec = get_spec("catia_sketch_circle")

        assert spec is not None
        assert declares_polar(spec.parameters) is True

    def test_a_tool_that_does_not_is_not(self) -> None:
        spec = get_spec("catia_pad")

        assert spec is not None
        assert declares_polar(spec.parameters) is False

    def test_an_empty_schema_is_handled_rather_than_raising(self) -> None:
        assert declares_polar({}) is False


class TestThePairIsDeclaredTogether:
    def test_polar_placement_returns_both_and_only_both(self) -> None:
        assert tuple(param.name for param in polar_placement()) == POLAR_KEYS

    def test_both_are_optional(self) -> None:
        assert not any(param.required for param in polar_placement())

    def test_both_are_consumed_by_the_server(self) -> None:
        assert all(param.consumed_by_server for param in polar_placement())


class TestTheDispatcherConsumesItBeforeEitherBackend:
    """`_augment` is where the conversion happens for the product path.

    Tested directly rather than through `call_catia`, which needs a database:
    what is being pinned is that the polar keys are gone and `at` is present by
    the time anything backend-shaped sees the call.
    """

    def test_a_polar_call_arrives_at_the_backend_as_cartesian(self) -> None:
        from app.catia.dispatch import _augment

        out = _augment(
            "catia_sketch_circle",
            {"diameter_mm": 9.0, "at_radius_mm": 35.0, "at_angle_deg": 45.0},
            get_spec("catia_sketch_circle"),
        )

        assert out["at"] == pytest.approx([CORNER_MM, CORNER_MM])
        assert not set(POLAR_KEYS) & set(out)

    def test_a_tool_without_polar_placement_is_untouched(self) -> None:
        from app.catia.dispatch import _augment

        arguments = {"sketch": "@plate.outline", "length_mm": 12.0}

        assert _augment("catia_pad", dict(arguments), get_spec("catia_pad")) == arguments

    def test_a_bad_placement_becomes_a_catia_error_the_model_can_read(self) -> None:
        from app.catia.dispatch import CatiaError, _augment

        with pytest.raises(CatiaError, match="catia_sketch_circle"):
            _augment(
                "catia_sketch_circle",
                {"diameter_mm": 9.0, "at_angle_deg": 45.0},
                get_spec("catia_sketch_circle"),
            )

    def test_it_still_works_when_no_spec_is_passed(self) -> None:
        """The parameter is defaulted, and a missing spec must not crash a turn."""
        from app.catia.dispatch import _augment

        assert _augment("catia_pad", {"length_mm": 1.0}) == {"length_mm": 1.0}


class TestItBuildsTheRightPartOnTheRealKernel:
    """The end of the story: the same words, through the geometry.

    `OcctRunner` is driven directly here because the design IR and the mission
    ladder do exactly that — the dispatcher is not on that path, so `_at` has to
    resolve polar itself and this is what says it does.
    """

    @staticmethod
    def _flange(runner):
        runner("catia_new_part", {"name": "Flange"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "outline", "width_mm": 100.0, "height_mm": 100.0},
        )
        return runner("catia_pad", {"sketch": "outline", "length_mm": 12.0})

    def test_four_holes_land_on_the_bolt_circle_that_was_asked_for(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        self._flange(runner)
        runner("catia_sketch_create", {"support": "XY", "name": "holes"})
        for angle in (45.0, 135.0, 225.0, 315.0):
            runner(
                "catia_sketch_circle",
                {
                    "sketch": "holes",
                    "diameter_mm": 9.0,
                    "at_radius_mm": BOLT_CIRCLE_RADIUS_MM,
                    "at_angle_deg": angle,
                },
            )

        result = runner("catia_pocket", {"sketch": "holes", "through_all": True})

        removed = 4.0 * math.pi * 4.5**2 * 12.0
        assert result["volume_mm3"] == pytest.approx(
            100.0 * 100.0 * 12.0 - removed, abs=1e-6
        )

    def test_the_wrong_answer_the_model_used_to_give_is_a_different_part(self) -> None:
        """(35, 35) is a 99 mm bolt circle. Nothing measured the difference
        before, which is why every call could return ok on a wrong flange."""
        assert math.hypot(35.0, 35.0) == pytest.approx(49.497, abs=1e-3)
        assert math.hypot(CORNER_MM, CORNER_MM) == pytest.approx(35.0)

    def test_a_polar_placed_circle_is_centred_where_the_angle_says(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner(
            "catia_sketch_circle",
            {"sketch": "s", "diameter_mm": 10.0, "at_radius_mm": 20.0,
             "at_angle_deg": 90.0},
        )

        result = runner("catia_pad", {"sketch": "s", "length_mm": 5.0})
        centre = result["centre_of_mass_mm"]

        assert centre[0] == pytest.approx(0.0, abs=1e-6)
        assert centre[1] == pytest.approx(20.0, abs=1e-6)

    def test_the_kernel_refuses_a_contradictory_placement_as_a_geometry_error(
        self,
    ) -> None:
        from app.kernel import OcctRunner
        from app.kernel.errors import GeometryError

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})

        with pytest.raises(GeometryError, match="not both"):
            runner(
                "catia_sketch_circle",
                {"sketch": "s", "diameter_mm": 10.0, "at": [1.0, 2.0],
                 "at_radius_mm": 20.0},
            )
