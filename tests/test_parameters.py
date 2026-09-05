"""`catia_set_parameter` on the open kernel — the tool rung 3 could not do without.

The system prompt says *"Dimensions are parameters. Prefer catia_set_parameter
over rebuilding a feature"*, and on `GEOMETRY_BACKEND=occt` that tool did not
exist. Measured end to end on 2026-09-05, driving the real chat endpoint with
rung 3 of the ladder — "the plate has to weigh 2.4 kg; adjust the thickness until
the measured mass is within 20 grams" — the model built the plate, measured it,
and then had no way to change a dimension. It did the only other thing available
and padded the same sketch again at four different lengths. Every call returned
`ok`. The part ended as seven stacked pads weighing 2.958 kg and the model
reported that as the answer.

A part built in conversation has no parameter set, so its build log is one: every
numeric argument of every mutating call is a dimension, and setting one rewrites
the call and replays the part from the top. These tests pin the three properties
that make that safe to offer — the replay is faithful, a value that will not
build changes nothing, and a wrong unit is refused rather than silently ignored.
"""

from __future__ import annotations

import math

import pytest

from app.kernel.errors import GeometryError

STEEL_KG_M3 = 7850.0
#: 200 x 150 plate, 60 mm bore, four 12 mm corner holes — rung 3's part.
PLATE_AREA_MM2 = 200.0 * 150.0 - math.pi * 30.0**2 - 4.0 * math.pi * 6.0**2
#: The thickness that puts it at 2.4 kg. Not a round number, which is the point:
#: it can only be arrived at by measuring.
TARGET_THICKNESS_MM = (2.4 / STEEL_KG_M3 * 1e9) / PLATE_AREA_MM2


def _plate(runner, thickness=10.0):
    runner("catia_new_part", {"name": "Plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner(
        "catia_sketch_rectangle",
        {"sketch": "outline", "width_mm": 200.0, "height_mm": 150.0},
    )
    runner("catia_pad", {"sketch": "outline", "length_mm": thickness})
    runner(
        "catia_set_material",
        {"material": "steel-s235", "density_kg_m3": STEEL_KG_M3},
    )
    return runner


class TestTheDimensionsAreVisible:
    def test_a_pad_length_is_a_parameter(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        names = [p["name"] for p in runner("catia_list_parameters", {})["parameters"]]

        assert "Pad.1\\length_mm" in names

    def test_the_sketch_dimensions_are_parameters_too(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        names = [p["name"] for p in runner("catia_list_parameters", {})["parameters"]]

        assert "outline\\width_mm" in names
        assert "outline\\height_mm" in names

    def test_each_carries_its_unit(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        by_name = {p["name"]: p for p in runner("catia_list_parameters", {})["parameters"]}

        assert by_name["Pad.1\\length_mm"]["unit"] == "mm"
        assert by_name["Pad.1\\length_mm"]["value"] == 10.0

    def test_the_filter_narrows_by_substring(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        found = runner("catia_list_parameters", {"filter": "width"})["parameters"]

        assert [p["name"] for p in found] == ["outline\\width_mm"]

    def test_a_count_is_not_offered_as_a_dimension(self) -> None:
        """`sides` changes what the feature is, not how big it is. Offering it
        here invites a model to set 6.5 sides."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "hex"})
        runner("catia_sketch_polygon", {"sketch": "hex", "sides": 6, "diameter_mm": 20.0})

        names = [p["name"] for p in runner("catia_list_parameters", {})["parameters"]]

        assert "hex\\sides" not in names
        assert "hex\\diameter_mm" in names

    def test_nothing_is_recorded_before_a_part_exists(self) -> None:
        from app.kernel import OcctRunner

        with pytest.raises(GeometryError, match="No document is open"):
            OcctRunner()("catia_list_parameters", {})


class TestSettingOneRebuildsThePart:
    def test_the_thickness_moves_and_the_mass_follows(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        out = runner(
            "catia_set_parameter",
            {"name": "Pad.1\\length_mm", "value": 20.0, "unit": "mm"},
        )

        assert out["previous_value"] == 10.0
        assert out["bounding_box_mm"]["size"][2] == pytest.approx(20.0, abs=1e-6)
        assert out["mass_kg"] == pytest.approx(200.0 * 150.0 * 20.0 * STEEL_KG_M3 * 1e-9)

    def test_rung_three_can_actually_be_done(self) -> None:
        """The whole point: measure, compute, set, measure again, hit the target."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Plate"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner("catia_sketch_rectangle",
               {"sketch": "outline", "width_mm": 200.0, "height_mm": 150.0})
        runner("catia_pad", {"sketch": "outline", "length_mm": 10.0})
        runner("catia_sketch_create", {"support": "XY", "name": "bore"})
        runner("catia_sketch_circle", {"sketch": "bore", "diameter_mm": 60.0})
        runner("catia_pocket", {"sketch": "bore", "through_all": True})
        runner("catia_sketch_create", {"support": "XY", "name": "corners"})
        for u, v in ((80.0, 55.0), (-80.0, 55.0), (-80.0, -55.0), (80.0, -55.0)):
            runner("catia_sketch_circle",
                   {"sketch": "corners", "diameter_mm": 12.0, "at": [u, v]})
        runner("catia_pocket", {"sketch": "corners", "through_all": True})
        runner("catia_set_material",
               {"material": "steel-s235", "density_kg_m3": STEEL_KG_M3})

        measured = runner("catia_measure", {})["mass_kg"]
        wanted = measured * TARGET_THICKNESS_MM / 10.0  # what 10 mm scales to

        out = runner(
            "catia_set_parameter",
            {"name": "Pad.1\\length_mm", "value": TARGET_THICKNESS_MM, "unit": "mm"},
        )

        assert out["mass_kg"] == pytest.approx(wanted)
        assert abs(out["mass_kg"] - 2.4) < 0.02, "within the 20 grams that was asked for"

    def test_a_feature_built_after_the_change_moves_with_it(self) -> None:
        """The reason this is a replay and not a patch: a through pocket cut
        before the thickness changed must still go all the way through."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        runner("catia_sketch_create", {"support": "XY", "name": "bore"})
        runner("catia_sketch_circle", {"sketch": "bore", "diameter_mm": 60.0})
        runner("catia_pocket", {"sketch": "bore", "through_all": True})

        out = runner(
            "catia_set_parameter",
            {"name": "Pad.1\\length_mm", "value": 25.0, "unit": "mm"},
        )

        expected = (200.0 * 150.0 - math.pi * 30.0**2) * 25.0
        assert out["volume_mm3"] == pytest.approx(expected, abs=1e-6)

    def test_the_feature_names_survive_the_rebuild(self) -> None:
        """Replay allocates the same names in the same order, so a design that
        referred to Pad.1 still refers to the same thing afterwards."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        runner("catia_fillet", {"radius_mm": 5.0, "edges": "vertical"})
        before = runner("catia_list_features", {})["features"]

        runner(
            "catia_set_parameter",
            {"name": "Pad.1\\length_mm", "value": 12.0, "unit": "mm"},
        )

        assert runner("catia_list_features", {})["features"] == before

    def test_a_sketch_dimension_can_be_set_too(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        out = runner(
            "catia_set_parameter",
            {"name": "outline\\width_mm", "value": 250.0, "unit": "mm"},
        )

        assert out["bounding_box_mm"]["size"][0] == pytest.approx(250.0, abs=1e-6)

    def test_setting_twice_composes(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        runner("catia_set_parameter",
               {"name": "Pad.1\\length_mm", "value": 12.0, "unit": "mm"})
        out = runner("catia_set_parameter",
                     {"name": "Pad.1\\length_mm", "value": 14.0, "unit": "mm"})

        assert out["previous_value"] == 12.0
        assert out["bounding_box_mm"]["size"][2] == pytest.approx(14.0, abs=1e-6)

    def test_the_set_itself_is_not_recorded(self) -> None:
        """Recording it would make every later replay re-apply every past edit."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        before = len(runner("catia_list_parameters", {})["parameters"])

        runner("catia_set_parameter",
               {"name": "Pad.1\\length_mm", "value": 12.0, "unit": "mm"})

        assert len(runner("catia_list_parameters", {})["parameters"]) == before

    def test_a_forward_slash_is_accepted(self) -> None:
        """CATIA writes a backslash; a model that has not seen a seat writes a
        slash, and refusing it teaches nothing."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        out = runner("catia_set_parameter",
                     {"name": "Pad.1/length_mm", "value": 15.0, "unit": "mm"})

        assert out["bounding_box_mm"]["size"][2] == pytest.approx(15.0, abs=1e-6)


class TestAFailedSetLeavesThePartExactlyAsItWas:
    """The property that makes this safe to hand an agent that will get it wrong."""

    def test_a_value_that_cannot_build_is_refused(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        with pytest.raises(GeometryError):
            runner("catia_set_parameter",
                   {"name": "Pad.1\\length_mm", "value": -5.0, "unit": "mm"})

    def test_and_the_part_is_still_there_afterwards(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        before = runner("catia_measure", {})

        with pytest.raises(GeometryError):
            runner("catia_set_parameter",
                   {"name": "Pad.1\\length_mm", "value": 0.0, "unit": "mm"})

        assert runner("catia_measure", {})["volume_mm3"] == before["volume_mm3"]

    def test_a_failure_further_down_the_part_says_so(self) -> None:
        """A fillet that the new thickness cannot carry is not a bad thickness —
        the agent has to be able to tell the two apart to act on either."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        runner("catia_fillet", {"radius_mm": 4.0, "edges": "horizontal"})

        with pytest.raises(GeometryError, match="further down the part"):
            runner("catia_set_parameter",
                   {"name": "Pad.1\\length_mm", "value": 1.0, "unit": "mm"})

    def test_the_part_survives_that_too(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        runner("catia_fillet", {"radius_mm": 4.0, "edges": "horizontal"})
        before = runner("catia_measure", {})["volume_mm3"]

        with pytest.raises(GeometryError):
            runner("catia_set_parameter",
                   {"name": "Pad.1\\length_mm", "value": 1.0, "unit": "mm"})

        assert runner("catia_measure", {})["volume_mm3"] == before


class TestItRefusesWhatItCannotDo:
    def test_an_unknown_parameter_lists_the_real_ones(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        with pytest.raises(GeometryError, match=r"Pad\.1\\length_mm"):
            runner("catia_set_parameter",
                   {"name": "Pad.1\\depth_mm", "value": 5.0, "unit": "mm"})

    def test_a_name_that_matches_nothing_lists_what_there_is(self) -> None:
        """A bare *unambiguous* dimension is accepted — see
        `TestEverySpellingAModelCanProduceIsAccepted`. A name that matches
        nothing at all still has to say what the part actually has."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        with pytest.raises(GeometryError, match="No parameter named"):
            runner("catia_set_parameter",
                   {"name": "wall_thickness_mm", "value": 5.0, "unit": "mm"})

    def test_the_wrong_unit_is_refused_rather_than_ignored(self) -> None:
        """CATIA's own reason: a parameter is typed, and setting a length in
        degrees is a no-op that leaves the part looking unchanged."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        with pytest.raises(GeometryError, match="is in mm, not"):
            runner("catia_set_parameter",
                   {"name": "Pad.1\\length_mm", "value": 12.0, "unit": "deg"})

    def test_a_value_that_is_not_a_number_is_refused(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        with pytest.raises(GeometryError, match="needs a number"):
            runner("catia_set_parameter",
                   {"name": "Pad.1\\length_mm", "value": "thick", "unit": "mm"})

    def test_a_boolean_is_not_a_dimension(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 20.0,
                                          "height_mm": 20.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 5.0, "symmetric": True})

        names = [p["name"] for p in runner("catia_list_parameters", {})["parameters"]]

        assert "Pad.1\\symmetric" not in names


class TestTheUnitComesFromTheArgumentName:
    from app.kernel.occt.operations.parameters import unit_of

    @pytest.mark.parametrize(
        ("argument", "unit"),
        [
            ("length_mm", "mm"),
            ("angle_deg", "deg"),
            ("area_mm2", "mm2"),
            ("volume_mm3", "mm3"),
            ("mass_kg", "kg"),
            ("sides", ""),
        ],
    )
    def test_each(self, argument: str, unit: str) -> None:
        from app.kernel.occt.operations.parameters import unit_of

        assert unit_of(argument) == unit

    def test_mm3_wins_over_mm(self) -> None:
        """Longest suffix first, or every volume reads as a length."""
        from app.kernel.occt.operations.parameters import unit_of

        assert unit_of("volume_mm3") != "mm"


class TestEverySpellingAModelCanProduceIsAccepted:
    """A backslash does not survive the round trip to a model and back.

    The tool payload is JSON, so a name containing a backslash is *shown* to the
    model with that backslash escaped — and it types back what it read. Measured
    on 2026-09-05: rung 3's agent called `catia_list_parameters`, copied the name
    it was handed, and had all four of its `catia_set_parameter` calls refused for
    punctuation. It then abandoned the parameter loop and padded a second slab
    over the part, which reached the target mass with the bore and the corner
    holes filled in — a part that weighed exactly what was asked and was not the
    part that was asked for.

    So no spelling a model can produce from what it was shown is refused.
    """

    SEPARATOR = chr(92)

    @pytest.mark.parametrize(
        "spelling",
        [
            "Pad.1" + chr(92) + "length_mm",
            "Pad.1" + chr(92) * 2 + "length_mm",  # what JSON showed it
            "Pad.1/length_mm",
            "Pad.1.length_mm",
            "pad.1" + chr(92) + "LENGTH_MM",
            "  Pad.1" + chr(92) + "length_mm  ",
        ],
    )
    def test_each_reaches_the_same_parameter(self, spelling: str) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        out = runner("catia_set_parameter",
                     {"name": spelling, "value": 16.0, "unit": "mm"})

        assert out["previous_value"] == 10.0
        assert out["bounding_box_mm"]["size"][2] == pytest.approx(16.0, abs=1e-6)

    def test_the_doubled_backslash_is_the_one_that_was_measured_failing(self) -> None:
        """Named on its own so the regression cannot be quietly dropped."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        out = runner(
            "catia_set_parameter",
            {"name": "Pad.1" + chr(92) * 2 + "length_mm", "value": 11.5, "unit": "mm"},
        )

        assert out["parameter"]["value"] == 11.5

    def test_a_bare_dimension_name_works_when_it_is_unambiguous(self) -> None:
        """An agent that has lost the prefix is not wrong about what it wants,
        and refusing an unambiguous request teaches it to stop asking."""
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        out = runner("catia_set_parameter",
                     {"name": "length_mm", "value": 18.0, "unit": "mm"})

        assert out["bounding_box_mm"]["size"][2] == pytest.approx(18.0, abs=1e-6)

    def test_an_ambiguous_bare_name_is_refused_rather_than_guessed(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())
        runner("catia_sketch_create", {"support": "XY", "name": "second"})
        runner("catia_sketch_rectangle",
               {"sketch": "second", "width_mm": 50.0, "height_mm": 50.0})
        runner("catia_pad", {"sketch": "second", "length_mm": 30.0})

        with pytest.raises(GeometryError, match="No parameter named"):
            runner("catia_set_parameter",
                   {"name": "length_mm", "value": 5.0, "unit": "mm"})

    def test_the_refusal_lists_names_in_the_spelling_it_accepts(self) -> None:
        from app.kernel import OcctRunner

        runner = _plate(OcctRunner())

        with pytest.raises(GeometryError, match=r"Pad\.1.length_mm"):
            runner("catia_set_parameter",
                   {"name": "Pad.1" + chr(92) + "nonsense_mm", "value": 1.0,
                    "unit": "mm"})
