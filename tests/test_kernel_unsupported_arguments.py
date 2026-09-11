"""An argument the schema advertises is honoured, or refused — never ignored.

The registry in `app/catia/ops/` is the single declaration every surface is built
from, so a parameter that appears there has been described to the model in words
it will believe. Reading it on one backend and dropping it on the other is a
product that behaves differently depending on a setting nobody in the
conversation can see — the class measured twice on 2026-09-11 (`include_sketches`
on `catia_list_features`, then this).

**How the eighteen were found.** Not by reading code: a static scan cannot see a
function-local import, and it called `at_radius_mm` unread when `_at` resolves it
through `app.catia.ops.placement`. The trustworthy question is the empirical one
— build the part without the argument, build it again with a meaningfully
different value, compare the geometry — and that is what the differential tests
at the bottom of this file keep asking. **A differential is only as good as the
difference**: `target_body` was first recorded as ignored on a comparison against
`"PartBody"`, which was already the active body, so the measurement was of the
*harmless* value and proved nothing. The full suite caught it.

Five of the eighteen produced a confidently wrong part rather than a missing one:

* `catia_translate(direction=[1,0,0], distance_mm=50)` moved the part **1 mm**,
  and `distance_mm` is *required*. **Implemented**, not refused.
* `catia_pad(thin=True, thickness_mm=3)` returned a solid pad — 120,000 mm3 where
  a 3 mm wall is about 18,480.
* `catia_hole_at(thread='M6x1')` drilled a plain clearance hole.
* `plane` on the sketch primitives silently drew onto whatever sketch was open.
  **Implemented**, not refused.
* `catia_boolean(target_body=...)` always combined into the *active* body, and a
  body name that did not exist was accepted in silence. **Checked in the handler**
  rather than here, because its harmless value is not a constant.

Offline: no seat, no database, no bridge.
"""

from __future__ import annotations

import pytest

from app.catia.ops import registry
from app.kernel import available
from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.unsupported import IGNORED, ignored_argument_count

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

#: What the table held when this file was written. **It may only go down.**
#: Every entry is a capability somebody could implement; a number that is allowed
#: to rise is a licence to add a new silent gap and call it declared.
KNOWN_UNHONOURED = 14


def _part():  # type: ignore[no-untyped-def]
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "P"})
    return runner


def _plate():  # type: ignore[no-untyped-def]
    runner = _part()
    runner("catia_sketch_create", {"name": "S", "support": "XY"})
    runner("catia_sketch_rectangle", {"sketch": "S", "width_mm": 100, "height_mm": 60})
    runner("catia_pad", {"sketch": "S", "length_mm": 20})
    return runner


class TestTheTableDescribesRealArguments:
    """A table that drifts from the registry stops meaning anything."""

    def test_every_listed_operation_exists(self) -> None:
        for tool in IGNORED:
            assert registry.get(tool) is not None, tool

    def test_every_listed_argument_is_actually_declared(self) -> None:
        """Refusing an argument nobody advertises would be refusing a typo."""
        for tool, entries in IGNORED.items():
            declared = {p.name for p in registry.get(tool).params}
            for name in entries:
                assert name in declared, f"{tool}.{name} is not a declared parameter"

    def test_every_reason_says_what_to_do_instead(self) -> None:
        """A refusal the agent cannot act on costs a turn and teaches it nothing."""
        for tool, entries in IGNORED.items():
            for name, entry in entries.items():
                assert len(entry.reason) > 40, f"{tool}.{name}"
                assert not entry.reason.endswith("."), f"{tool}.{name} — composed into a sentence"

    def test_the_count_may_only_shrink(self) -> None:
        """Implementing one of these lowers it. Nothing may raise it.

        The same shape as `tests/test_delivery.py`'s grandfathered names: a list
        of known gaps is only honest while it is closing.
        """
        assert ignored_argument_count() <= KNOWN_UNHONOURED, (
            "An argument was added to the unhonoured table. That makes a new silent "
            "gap legal — implement it, or argue for raising KNOWN_UNHONOURED here."
        )


class TestASilentlyIgnoredArgumentIsNowRefused:
    def test_a_thin_pad_is_refused_rather_than_built_solid(self) -> None:
        """The measured case: 120,000 mm3 returned for a 3 mm wall of ~18,480."""
        runner = _part()
        runner("catia_sketch_create", {"name": "S", "support": "XY"})
        runner("catia_sketch_rectangle", {"sketch": "S", "width_mm": 100, "height_mm": 60})
        with pytest.raises(OperationNotSupported) as caught:
            runner("catia_pad", {"sketch": "S", "length_mm": 20, "thin": True, "thickness_mm": 3})
        message = str(caught.value)
        assert "thin" in message
        assert "catia_pad" in message
        assert "catia_shell" in message, "the refusal must name the way round it"

    def test_the_default_value_still_builds(self) -> None:
        """`thin: false` asks for nothing it will not get, so it is not an error.

        Refusing it would be the over-refusal `app/catia/` warns about: a model
        that spells out a default would be told its call is unsupported.
        """
        runner = _part()
        runner("catia_sketch_create", {"name": "S", "support": "XY"})
        runner("catia_sketch_rectangle", {"sketch": "S", "width_mm": 100, "height_mm": 60})
        built = runner("catia_pad", {"sketch": "S", "length_mm": 20, "thin": False})
        assert built["volume_mm3"] == pytest.approx(120_000.0, abs=1e-3)

    def test_a_default_that_is_true_is_the_harmless_one(self) -> None:
        """`catia_bill_of_materials.recursive` defaults to *true*, so `true` is the
        no-op and `false` is the request this backend cannot meet. A table that
        assumed every flag defaults to false would have these the wrong way round."""
        entry = IGNORED["catia_bill_of_materials"]["recursive"]
        assert entry.is_meaningful(False) is True
        assert entry.is_meaningful(True) is False

    def test_an_argument_that_is_absent_is_not_refused(self) -> None:
        """The guard must be invisible to every call that does not ask for a gap —
        it sits in `OcctRunner.__call__`, in front of all 116 operations."""
        runner = _plate()
        assert runner("catia_measure", {})["volume_mm3"] == pytest.approx(120_000.0, abs=1e-3)

    @pytest.mark.parametrize(
        ("tool", "name"),
        [(tool, name) for tool, entries in IGNORED.items() for name in entries],
    )
    def test_the_refusal_names_the_argument_and_the_operation(self, tool: str, name: str) -> None:
        """Every entry, not just the ones with a convenient fixture.

        Asserted on the guard rather than through a built part, because several
        of these operations need an assembly or a seed feature to reach at all —
        and what is being pinned is that the refusal is legible, which does not
        depend on the geometry.
        """
        from app.kernel.occt.unsupported import refuse_unhonoured

        entry = IGNORED[tool][name]
        asking = {True: False, False: True, "PartBody": "Other"}.get(entry.harmless, 1)
        with pytest.raises(OperationNotSupported) as caught:
            refuse_unhonoured(tool, {name: asking})
        assert name in str(caught.value)
        assert tool in str(caught.value)


class TestABooleanCombinesIntoTheBodyItWasToldTo:
    """`target_body` is declared — "The body to combine into" — and is read by
    nothing in `app/`. Measured 2026-09-11: varying it alone, including to a body
    that does not exist, changed nothing. A caller who activated one body and
    named another got the wrong one silently.

    Checked in the handler rather than in `IGNORED`, because the value that is
    harmless is not a constant — it is whatever body happens to be active — and
    only the document knows that. That is the boundary of what a static table of
    arguments can express, and it is worth knowing where it is.
    """

    def _peanut(self):  # type: ignore[no-untyped-def]
        runner = _part()
        runner("catia_body_create", {"name": "lower"})
        runner("catia_surface_primitive",
               {"kind": "sphere", "centre": [0, 0, 0], "radius_mm": 10.0, "name": "near"})
        runner("catia_body_create", {"name": "upper"})
        runner("catia_surface_primitive",
               {"kind": "sphere", "centre": [0, 0, 15], "radius_mm": 10.0, "name": "far"})
        runner("catia_body_activate", {"body": "lower"})
        return runner

    def test_naming_the_active_body_is_honoured(self) -> None:
        """It asks for exactly what it will get, so refusing it would be the
        over-refusal `app/catia/` warns about."""
        built = self._peanut()(
            "catia_boolean",
            {"operation": "union", "target_body": "lower", "tool_body": "upper"},
        )
        assert built["volume_mm3"] > 0

    def test_naming_another_body_is_refused_rather_than_ignored(self) -> None:
        with pytest.raises(GeometryError) as caught:
            self._peanut()(
                "catia_boolean",
                {"operation": "union", "target_body": "upper", "tool_body": "lower"},
            )
        assert "upper" in str(caught.value) and "lower" in str(caught.value)
        assert "catia_body_activate" in str(caught.value)

    def test_a_body_that_does_not_exist_is_refused(self) -> None:
        """It used to be accepted in silence, which is the same defect wearing a
        typo: the boolean ran, into a body nobody named."""
        with pytest.raises(GeometryError):
            self._peanut()(
                "catia_boolean",
                {"operation": "union", "target_body": "nonexistent", "tool_body": "upper"},
            )


class TestTranslateMovesTheDistanceItWasGiven:
    """`distance_mm` is required and was never read. Measured 2026-09-11: a part
    asked to move 50 mm moved 1 mm, and said `ok`."""

    def _moved(self, arguments: dict) -> float:  # type: ignore[type-arg]
        runner = _plate()
        box = runner("catia_translate", arguments)["bounding_box_mm"]
        return float(box["min"][0])

    def test_fifty_millimetres_means_fifty(self) -> None:
        # The plate spans -50..50 in x, so moving +50 puts its minimum at 0.
        assert self._moved({"direction": [1, 0, 0], "distance_mm": 50}) == pytest.approx(
            0.0, abs=1e-3
        )

    def test_the_direction_is_normalised_so_its_length_carries_no_meaning(self) -> None:
        """This is the defect in one assertion: the magnitude used to *be* the
        distance, so [2,0,0] and [1,0,0] moved the part different amounts."""
        assert self._moved({"direction": [2, 0, 0], "distance_mm": 50}) == pytest.approx(
            self._moved({"direction": [1, 0, 0], "distance_mm": 50}), abs=1e-6
        )

    def test_a_negative_distance_reverses_it(self) -> None:
        assert self._moved({"direction": [1, 0, 0], "distance_mm": -50}) == pytest.approx(
            -100.0, abs=1e-3
        )

    def test_a_zero_direction_is_refused_rather_than_dividing_by_zero(self) -> None:
        runner = _plate()
        with pytest.raises(GeometryError, match="zero length"):
            runner("catia_translate", {"direction": [0, 0, 0], "distance_mm": 50})

    def test_an_explicit_vector_is_still_a_whole_displacement(self) -> None:
        """`vector` is not in the registry and is used by callers inside this repo
        that drive the runner directly. Keeping it is what stops this fix
        rewriting the meaning of a call the design IR already makes."""
        assert self._moved({"vector": [1, 0, 0]}) == pytest.approx(-49.0, abs=1e-3)


class TestASketchPrimitiveHonoursItsPlane:
    """`plane` is documented as "Support to sketch on when no sketch is open" and
    did nothing here — the profile went onto whatever sketch happened to be open."""

    def test_it_opens_a_sketch_when_none_is(self) -> None:
        runner = _part()
        drawn = runner("catia_sketch_rectangle", {"plane": "YZ", "width_mm": 20, "height_mm": 20})
        assert drawn["support"] == "YZ"
        assert drawn["profiles"] == 1

    def test_the_profile_really_lands_on_that_plane(self) -> None:
        """A support recorded in the payload is not a profile in the right place."""
        runner = _part()
        drawn = runner("catia_sketch_rectangle", {"plane": "YZ", "width_mm": 20, "height_mm": 20})
        box = runner("catia_pad", {"sketch": drawn["sketch"], "length_mm": 5})["bounding_box_mm"]
        # Extruded along the YZ plane's normal, so 5 mm thick in x and 20 in y and z.
        assert [round(v) for v in box["size"]] == [5, 20, 20]

    def test_it_agrees_with_a_named_sketch_without_complaint(self) -> None:
        runner = _part()
        runner("catia_sketch_create", {"name": "S", "support": "XY"})
        drawn = runner(
            "catia_sketch_rectangle",
            {"sketch": "S", "plane": "XY", "width_mm": 10, "height_mm": 10},
        )
        assert drawn["profiles"] == 1

    def test_a_plane_that_contradicts_the_named_sketch_is_refused(self) -> None:
        """Silently picking one of the two is a profile on a plane nobody asked
        for, and both spellings are in the same call to be compared."""
        runner = _part()
        runner("catia_sketch_create", {"name": "S", "support": "XY"})
        with pytest.raises(GeometryError) as caught:
            runner(
                "catia_sketch_rectangle",
                {"sketch": "S", "plane": "YZ", "width_mm": 10, "height_mm": 10},
            )
        assert "YZ" in str(caught.value) and "XY" in str(caught.value)

    @pytest.mark.parametrize(
        ("tool", "extra"),
        [
            ("catia_sketch_circle", {"diameter_mm": 20}),
            ("catia_sketch_polygon", {"sides": 6, "diameter_mm": 20}),
        ],
    )
    def test_the_other_primitives_honour_it_too(self, tool: str, extra: dict) -> None:  # type: ignore[type-arg]
        runner = _part()
        drawn = runner(tool, {"plane": "YZ", **extra})
        assert drawn["support"] == "YZ"


class TestTheDifferentialQuestionIsAskedOfTheThingsThatWereFixed:
    """Supplying the argument must change the answer. This is the check that
    found all eighteen, and the only one a function-local import cannot fool."""

    def test_distance_changes_where_the_part_ends_up(self) -> None:
        one = _plate()("catia_translate", {"direction": [1, 0, 0], "distance_mm": 1})
        fifty = _plate()("catia_translate", {"direction": [1, 0, 0], "distance_mm": 50})
        assert one["bounding_box_mm"] != fifty["bounding_box_mm"]

    def test_plane_changes_where_the_profile_ends_up(self) -> None:
        def size(plane: str) -> list[float]:
            runner = _part()
            drawn = runner(tool_name := "catia_sketch_rectangle", {
                "plane": plane, "width_mm": 20, "height_mm": 20
            })
            assert tool_name
            box = runner("catia_pad", {"sketch": drawn["sketch"], "length_mm": 5})
            return [round(v) for v in box["bounding_box_mm"]["size"]]

        assert size("XY") != size("YZ")
