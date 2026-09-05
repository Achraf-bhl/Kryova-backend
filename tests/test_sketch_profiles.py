"""A profile inside another is a hole; a profile beside it is a second region.

`Sketch.face`'s docstring already said the first half — *"Later profiles become
holes in the first. That is what a sketch containing an outer rectangle and an
inner circle means, and building it any other way makes a pad that ignores its
own bore."* It did not do it. Every profile after the first went to
`BRepBuilderAPI_MakeFace.Add`, and OCCT requires an inner wire to carry the
**opposite** orientation to the outer one; an unreversed wire is accepted without
complaint and integrates as material.

Measured on 2026-09-05: a 100x100 sketch with a 40 mm circle in it, padded 10 mm,
came back at 112,566 mm³. The plate minus its bore is 87,434. The difference is a
boss where the bore should be — plate *plus* circle rather than plate *minus* it.
`IsDone()` was true and nothing anywhere said otherwise.

Found by driving rung 3 of the ladder: asked for a plate with a bore and four
corner holes, the model drew all six profiles into one sketch and padded once,
which is the natural reading of the request and is what CATIA does. It got a
plate with six bosses on it.

Containment is now decided by boolean algebra — each profile intersected with the
region built so far — rather than by wire order. Exact, not sampled, and
independent of the order the profiles were drawn in, which matters because an
agent draws the bore before the outline about as often as after it.
"""

from __future__ import annotations

import math

import pytest

from app.kernel.errors import GeometryError

PLATE = 100.0
THICK = 10.0
BORE_D = 40.0

PLATE_SOLID_MM3 = PLATE * PLATE * THICK
BORE_MM3 = math.pi * (BORE_D / 2.0) ** 2 * THICK


def _sketch(runner, name="s"):
    runner("catia_new_part", {"name": "P"})
    runner("catia_sketch_create", {"support": "XY", "name": name})
    return runner


class TestAnInnerProfileIsAHole:
    def test_a_plate_with_a_bore_weighs_less_than_the_plate(self) -> None:
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": PLATE,
                                          "height_mm": PLATE})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": BORE_D})

        built = runner("catia_pad", {"sketch": "s", "length_mm": THICK})

        assert built["volume_mm3"] == pytest.approx(PLATE_SOLID_MM3 - BORE_MM3, abs=1e-6)

    def test_it_is_not_the_plate_plus_a_boss(self) -> None:
        """The number the broken version returned, named so it cannot come back."""
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": PLATE,
                                          "height_mm": PLATE})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": BORE_D})

        built = runner("catia_pad", {"sketch": "s", "length_mm": THICK})

        assert built["volume_mm3"] != pytest.approx(PLATE_SOLID_MM3 + BORE_MM3, abs=1e-6)

    def test_the_order_the_profiles_were_drawn_in_does_not_matter(self) -> None:
        """An agent draws the bore before the outline about as often as after."""
        from app.kernel import OcctRunner

        outline_first = _sketch(OcctRunner())
        outline_first("catia_sketch_rectangle", {"sketch": "s", "width_mm": PLATE,
                                                 "height_mm": PLATE})
        outline_first("catia_sketch_circle", {"sketch": "s", "diameter_mm": BORE_D})

        bore_first = _sketch(OcctRunner())
        bore_first("catia_sketch_circle", {"sketch": "s", "diameter_mm": BORE_D})
        bore_first("catia_sketch_rectangle", {"sketch": "s", "width_mm": PLATE,
                                              "height_mm": PLATE})

        a = outline_first("catia_pad", {"sketch": "s", "length_mm": THICK})
        b = bore_first("catia_pad", {"sketch": "s", "length_mm": THICK})

        assert a["volume_mm3"] == pytest.approx(b["volume_mm3"], abs=1e-6)
        assert b["volume_mm3"] == pytest.approx(PLATE_SOLID_MM3 - BORE_MM3, abs=1e-6)

    def test_several_holes_in_one_outline(self) -> None:
        """Rung 3's part, as the model actually drew it: one sketch, six profiles."""
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 200.0,
                                          "height_mm": 150.0})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 60.0})
        for u, v in ((80.0, 55.0), (-80.0, 55.0), (-80.0, -55.0), (80.0, -55.0)):
            runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 12.0,
                                           "at": [u, v]})

        built = runner("catia_pad", {"sketch": "s", "length_mm": 11.442008074687148})

        expected = (
            200.0 * 150.0 - math.pi * 30.0**2 - 4.0 * math.pi * 6.0**2
        ) * 11.442008074687148
        assert built["volume_mm3"] == pytest.approx(expected, abs=1e-6)

    def test_and_that_part_weighs_what_was_asked_for(self) -> None:
        """2.4 kg, which is the whole of rung 3's requirement."""
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 200.0,
                                          "height_mm": 150.0})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 60.0})
        for u, v in ((80.0, 55.0), (-80.0, 55.0), (-80.0, -55.0), (80.0, -55.0)):
            runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 12.0,
                                           "at": [u, v]})
        runner("catia_pad", {"sketch": "s", "length_mm": 11.442008074687148})
        runner("catia_set_material", {"material": "steel-s235",
                                      "density_kg_m3": 7850.0})

        assert runner("catia_measure", {})["mass_kg"] == pytest.approx(2.4, abs=1e-4)


class TestASeparateProfileIsASecondRegion:
    def test_two_disjoint_circles_pad_as_two_bosses(self) -> None:
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 10.0,
                                       "at": [-20.0, 0.0]})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 10.0,
                                       "at": [20.0, 0.0]})

        built = runner("catia_pad", {"sketch": "s", "length_mm": THICK})

        assert built["volume_mm3"] == pytest.approx(
            2.0 * math.pi * 5.0**2 * THICK, abs=1e-6
        )
        assert built["solid_count"] == 2

    def test_the_flange_bolt_holes_still_work(self) -> None:
        """Rung 2's hole sketch — four circles in one sketch, pocketed. This is
        the case the *old* behaviour got right, and it must stay right."""
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner(), name="outline")
        runner("catia_sketch_rectangle", {"sketch": "outline", "width_mm": PLATE,
                                          "height_mm": PLATE})
        runner("catia_pad", {"sketch": "outline", "length_mm": 12.0})
        runner("catia_sketch_create", {"support": "XY", "name": "holes"})
        for angle in (45.0, 135.0, 225.0, 315.0):
            runner("catia_sketch_circle", {"sketch": "holes", "diameter_mm": 9.0,
                                           "at_radius_mm": 35.0,
                                           "at_angle_deg": angle})

        built = runner("catia_pocket", {"sketch": "holes", "through_all": True})

        removed = 4.0 * math.pi * 4.5**2 * 12.0
        assert built["volume_mm3"] == pytest.approx(
            PLATE * PLATE * 12.0 - removed, abs=1e-6
        )


class TestAPartialOverlapIsRefused:
    """Neither a hole nor a second region, and guessing between the two readings
    is how a part comes out plausible and wrong."""

    def test_two_circles_that_cross_are_refused(self) -> None:
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 40.0,
                                       "at": [0.0, 0.0]})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 40.0,
                                       "at": [20.0, 0.0]})

        with pytest.raises(GeometryError, match="partly overlap"):
            runner("catia_pad", {"sketch": "s", "length_mm": THICK})

    def test_the_refusal_says_what_to_do_instead(self) -> None:
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 40.0})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 30.0,
                                          "height_mm": 200.0})

        with pytest.raises(GeometryError, match="separate sketches"):
            runner("catia_pad", {"sketch": "s", "length_mm": THICK})


class TestNothingElseChanged:
    def test_a_single_profile_pads_exactly_as_before(self) -> None:
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0,
                                          "height_mm": 40.0})

        built = runner("catia_pad", {"sketch": "s", "length_mm": 20.0})

        assert built["volume_mm3"] == pytest.approx(60.0 * 40.0 * 20.0, abs=1e-6)
        assert built["face_count"] == 6

    def test_an_empty_sketch_is_still_refused_with_the_same_reason(self) -> None:
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())

        with pytest.raises(GeometryError, match="no closed profile"):
            runner("catia_pad", {"sketch": "s", "length_mm": 10.0})

    def test_a_revolved_profile_gets_the_same_treatment(self) -> None:
        """`face()` serves shaft and groove too, not only pad."""
        from app.kernel import OcctRunner

        runner = _sketch(OcctRunner())
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 10.0,
                                          "height_mm": 20.0, "at": [30.0, 0.0]})

        built = runner("catia_shaft", {"sketch": "s", "angle_deg": 360.0})

        # Pappus: a rectangle revolved about the sketch's vertical axis.
        assert built["volume_mm3"] == pytest.approx(
            2.0 * math.pi * 30.0 * (10.0 * 20.0), abs=1e-3
        )
