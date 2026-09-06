"""Two ways a part came out wrong with every call returning `ok` — gate G1.

Both were measured on 2026-09-06 through the real chat endpoint, on the third
rung-3 attempt of the session. Asked for a 200x150 steel plate with a 60 mm bore
and four 12 mm clearance holes, corrected until it weighed 2.4 kg, the agent
produced a plate with a bore, no holes, three stacked pads, and a closing message
saying the holes were done and the mass was on target.

Fifteen tool calls. Fourteen returned `ok`.

**The sketch that was drawn and abandoned.** It created a sketch called
`Hole Positions`, drew all four circles into it correctly, and then never
pocketed it. A sketch is not geometry — drawing one changes nothing about the
part — so an abandoned sketch is silent *by construction*: there is no failed
operation anywhere for anything to notice. But nobody draws four circles for no
reason, so it is an unusually clear statement of intent, and a measurement that
does not mention it is reporting a part that is not the part that was asked for.

**The same sketch padded three times.** Told to change the thickness, it padded
the outline at 10 mm, then 11 mm, then 11.5 mm. All three succeeded. The pads are
coincident so they fuse, and the geometry is right — the part really is 11.5 mm
thick — but the document ends carrying `Pad.1`, `Pad.2` and `Pad.3` where one pad
was meant, and there is no longer a single parameter to change. This is the same
intent that arrived on the *previous* attempt as
`catia_pad {"feature": "Pad.1", ...}` (see
`test_refusal_points_at_the_right_tool.py`); this is the door it takes when it
phrases the request legally.

Neither guard refuses a legitimate part. The abandoned sketch is reported, not
rejected — a sketch may reasonably be drawn before it is used. The repeated build
is refused only when the second call differs from the first in **nothing but a
dimension**, so a second pad in another direction, at an offset, or to a
different limit still passes: `CLAUDE.md` is explicit that an over-refusal is not
safe, because the agent's recovery from one becomes a wrongly built part.
"""

from __future__ import annotations

import pytest

from app.kernel.errors import GeometryError


def _outline_and_holes():
    """The run's own opening, up to the point it went wrong."""
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "bracket"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner("catia_sketch_rectangle", {"sketch": "outline", "width_mm": 200.0, "height_mm": 150.0})
    runner("catia_sketch_create", {"support": "XY", "name": "Hole Positions"})
    for angle in (0.0, 90.0, 180.0, 270.0):
        runner(
            "catia_sketch_circle",
            {
                "sketch": "Hole Positions",
                "diameter_mm": 12.0,
                "at_radius_mm": 100.0,
                "at_angle_deg": angle,
            },
        )
    return runner


class TestASketchThatWasNeverUsed:
    def test_it_is_reported(self) -> None:
        runner = _outline_and_holes()
        runner("catia_pad", {"sketch": "outline", "length_mm": 11.5})

        measured = runner("catia_measure", {})

        assert measured["unused_sketches"] == ["Hole Positions"]

    def test_using_it_makes_the_report_go_away(self) -> None:
        runner = _outline_and_holes()
        runner("catia_pad", {"sketch": "outline", "length_mm": 11.5})
        runner("catia_pocket", {"sketch": "Hole Positions", "through_all": True})

        measured = runner("catia_measure", {})

        assert "unused_sketches" not in measured

    def test_a_part_with_no_loose_sketches_says_nothing(self) -> None:
        """Absent rather than empty, so no payload a design already asserts
        against changes shape."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "plain"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 10.0})

        assert "unused_sketches" not in runner("catia_measure", {})

    def test_it_is_not_refused_only_reported(self) -> None:
        """A sketch may legitimately be drawn before it is used, so this must
        never become an error — only something the payload mentions."""
        runner = _outline_and_holes()

        built = runner("catia_pad", {"sketch": "outline", "length_mm": 11.5})

        assert built["has_solid"] is True

    def test_the_holes_really_were_missing(self) -> None:
        """The measurement that should have made this obvious and did not: the
        part weighs what a plate with no holes weighs."""
        import math

        runner = _outline_and_holes()
        runner("catia_pad", {"sketch": "outline", "length_mm": 11.5})

        measured = runner("catia_measure", {})
        no_holes = 200.0 * 150.0 * 11.5

        assert measured["volume_mm3"] == pytest.approx(no_holes, abs=1e-6)
        assert measured["volume_mm3"] > no_holes - 4 * math.pi * 6.0**2 * 11.5


class TestBuildingTheSameSketchTwice:
    def test_a_second_pad_at_a_new_length_is_refused(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "x"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 10.0})

        with pytest.raises(GeometryError, match="already been built"):
            runner("catia_pad", {"sketch": "s", "length_mm": 11.0})

    def test_the_refusal_names_catia_set_parameter_and_the_feature(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "x"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 10.0})

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"sketch": "s", "length_mm": 11.0})

        message = str(refused.value)
        assert "catia_set_parameter" in message
        assert "Pad.1" in message

    def test_the_name_it_offers_works_and_leaves_one_pad(self) -> None:
        """Same standard as the other refusal: advice that cannot be followed
        literally is worse than none."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "x"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 10.0})

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"sketch": "s", "length_mm": 11.5})
        offered = str(refused.value).split("name='")[1].split("'")[0]

        runner("catia_set_parameter", {"name": offered, "value": 11.5})
        measured = runner("catia_measure", {})

        assert measured["bounding_box_mm"]["size"][2] == pytest.approx(11.5, abs=1e-6)
        assert measured["features"] == ["Pad.1"]

    def test_a_genuinely_different_second_feature_still_builds(self) -> None:
        """The over-refusal this must not become. A pocket from the same sketch
        is a different tool and a different intent, and is allowed."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "x"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 10.0})
        runner("catia_sketch_create", {"support": "XY", "name": "hole"})
        runner("catia_sketch_circle", {"sketch": "hole", "diameter_mm": 8.0})

        built = runner("catia_pocket", {"sketch": "hole", "through_all": True})

        assert built["has_solid"] is True

    def test_a_different_sketch_at_the_same_length_is_untouched(self) -> None:
        """**Honestly unpinned**, and worth saying so rather than implying it is
        verified. Deleting the explicit sketch comparison from
        `_refuse_a_second_identical_build` leaves every test here passing,
        because the argument comparison below it already treats `sketch` as a
        distinguishing key and lets the call through. The two checks are defence
        in depth over the same fact, so no test can fail on one of them alone —
        both would have to be wrong together. The check stays because it states
        the rule the reader needs, and because a later edit to
        `_DIMENSION_ARGUMENTS` could remove the second line of defence without
        anyone noticing.
        """
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "x"})
        for name, offset in (("a", -40.0), ("b", 40.0)):
            runner("catia_sketch_create", {"support": "XY", "name": name})
            runner(
                "catia_sketch_circle",
                {"sketch": name, "diameter_mm": 10.0, "at": [offset, 0.0]},
            )
        runner("catia_pad", {"sketch": "a", "length_mm": 10.0})

        built = runner("catia_pad", {"sketch": "b", "length_mm": 10.0})

        assert built["has_solid"] is True


class TestAFeatureThatChangedNothing:
    """The third door the same failure takes — gate G1, fourth rung-3 attempt.

    Asked for four 12 mm clearance holes on a **200 mm bolt circle** in a
    200x150 plate, the agent drew four circles at a 100 mm radius and pocketed
    them. On a plate spanning x +/- 100 and y +/- 75, two of those sit centred on
    the left and right *edges* — half in the material, half in the air — and the
    other two, at y = +/- 100, are entirely off the part. The call returned `ok`.
    The finished plate has two small notches in its edges and no clearance holes,
    and the volume confirms it to the last digit:

        200*150*11.24 - pi*30^2*11.24 - 2*(half a 12 mm circle, 5 mm deep)
          = 304854.16 mm^3   — exactly what was measured.

    `BRepAlgoAPI_Cut` succeeds when the tool and the target do not overlap: the
    answer is the target, unchanged, and `IsDone()` is true. Nothing said the
    holes had missed.

    Refused rather than noted, unlike the abandoned sketch above, because there
    is no reading under which a caller meant a feature that changes no material.
    """

    def _plate(self):
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "p"})
        runner("catia_sketch_create", {"support": "XY", "name": "o"})
        runner("catia_sketch_rectangle", {"sketch": "o", "width_mm": 200.0, "height_mm": 150.0})
        runner("catia_pad", {"sketch": "o", "length_mm": 11.24})
        return runner

    def test_a_cut_that_misses_the_part_is_refused(self) -> None:
        runner = self._plate()
        runner("catia_sketch_create", {"support": "XY", "name": "miss"})
        runner(
            "catia_sketch_circle",
            {"sketch": "miss", "diameter_mm": 12.0, "at_radius_mm": 100.0, "at_angle_deg": 90.0},
        )

        with pytest.raises(GeometryError, match="removed no material"):
            runner("catia_pocket", {"sketch": "miss", "depth_mm": 5.0})

    def test_the_refusal_explains_why_a_miss_looks_like_a_success(self) -> None:
        """The half nobody would guess: an OCCT cut with no overlap is not an
        error, it returns the part unchanged."""
        runner = self._plate()
        runner("catia_sketch_create", {"support": "XY", "name": "miss"})
        runner(
            "catia_sketch_circle",
            {"sketch": "miss", "diameter_mm": 12.0, "at_radius_mm": 100.0, "at_angle_deg": 90.0},
        )

        with pytest.raises(GeometryError) as refused:
            runner("catia_pocket", {"sketch": "miss", "depth_mm": 5.0})

        message = str(refused.value)
        assert "does not overlap" in message
        assert "bolt circle" in message

    def test_a_boss_buried_inside_the_part_is_refused(self) -> None:
        """The adding half of the same rule: material that is already there."""
        runner = self._plate()
        runner("catia_sketch_create", {"support": "XY", "name": "buried"})
        runner("catia_sketch_circle", {"sketch": "buried", "diameter_mm": 10.0})

        with pytest.raises(GeometryError, match="added no material"):
            runner("catia_pad", {"sketch": "buried", "length_mm": 5.0})

    def test_a_hole_that_hits_still_works(self) -> None:
        runner = self._plate()
        runner("catia_sketch_create", {"support": "XY", "name": "hit"})
        runner("catia_sketch_circle", {"sketch": "hit", "diameter_mm": 12.0, "at": [60.0, 40.0]})

        built = runner("catia_pocket", {"sketch": "hit", "through_all": True})

        assert built["volume_mm3"] < 200.0 * 150.0 * 11.24

    def test_a_boss_that_protrudes_still_works(self) -> None:
        runner = self._plate()
        runner("catia_sketch_create", {"support": "XY", "name": "boss"})
        runner("catia_sketch_circle", {"sketch": "boss", "diameter_mm": 20.0})

        built = runner("catia_pad", {"sketch": "boss", "length_mm": 20.0})

        assert built["volume_mm3"] > 200.0 * 150.0 * 11.24

    def test_a_partial_miss_is_NOT_caught_and_that_is_stated(self) -> None:
        """**The honest limit of this guard.** Two of gate G1's four circles did
        overlap the plate, so the pocket as a whole removed 565 mm^3 and this
        guard stays silent — the part still ends with notches instead of holes.
        Catching a partial miss needs a per-profile check against the material,
        which is a larger piece of work and is not pretended here. Pinned so the
        gap is visible rather than discovered again at the next gate.
        """
        runner = self._plate()
        runner("catia_sketch_create", {"support": "XY", "name": "partial"})
        runner(
            "catia_sketch_circle",
            {"sketch": "partial", "diameter_mm": 12.0, "at_radius_mm": 100.0, "at_angle_deg": 0.0},
        )

        built = runner("catia_pocket", {"sketch": "partial", "depth_mm": 5.0})

        assert built["volume_mm3"] < 200.0 * 150.0 * 11.24
