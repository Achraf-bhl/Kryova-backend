"""A part that came out in pieces says so — found at gate G1, 2026-09-06.

`contract.py` has described `solid_count` since it was written as *"more than
one means the operation left the part in pieces, which is usually a defect and
never a warning on its own."* Nothing acted on it.

**What that cost, measured through the real chatbot.** Gate G1's rung-3 prompt
asked for a 200x150 steel plate with a 60 mm bore through the centre and four
12 mm clearance holes "20 mm in from each corner", to be corrected to 2.4 kg.
The model placed the four holes at `at_radius_mm: 20` — a 20 mm bolt circle,
i.e. 20 mm from the *centre*, which is **inside** the 60 mm bore. Each small
circle therefore landed in the hole rather than in the material, and a profile
that shares no area with the region is a separate region by the containment rule
in `sketching.Sketch.face` — so they came back as four loose posts standing in
the bore. The part was five disconnected solids.

Every number reported was correct:

    volume = 200*150*11 - pi*30^2*11 + 4*pi*6^2*11 = 303874.515 mm^3
    mass   = 2.391 kg

and 2.391 kg is **inside the 20 g tolerance the user asked for**. By
coincidence, on a part that is not a part. The agent reported success, the
measurement agreed, and nothing anywhere disagreed. It was caught by looking at
the render — which is exactly why `CLAUDE.md` says a run with no picture has not
been verified, it has been believed.

The fix is not a refusal. A multi-solid result is legitimate for a deliberate
multi-body design, and refusing it would break those. It is that the payload
**says** it, so that the number and its meaning travel together: the mass of five
disconnected solids is not the mass of a component, and a caller who does not
know that is being told something false in a true number.
"""

from __future__ import annotations

import math

import pytest

PLATE_X = 200.0
PLATE_Y = 150.0
THICK = 11.0
BORE_D = 60.0
HOLE_D = 12.0
HOLE_RADIUS = 20.0


def _plate_with_holes_inside_the_bore():
    """The part gate G1 actually built, reproduced exactly."""
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "G1"})
    runner("catia_sketch_create", {"support": "XY", "name": "s"})
    runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": PLATE_X, "height_mm": PLATE_Y})
    runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": BORE_D})
    for angle in (45.0, 135.0, 225.0, 315.0):
        runner(
            "catia_sketch_circle",
            {
                "sketch": "s",
                "diameter_mm": HOLE_D,
                "at_radius_mm": HOLE_RADIUS,
                "at_angle_deg": angle,
            },
        )
    runner("catia_pad", {"sketch": "s", "length_mm": THICK})
    runner("catia_set_material", {"material": "steel-1018"})
    return runner


class TestThePartFromGateG1:
    def test_it_really_does_come_out_in_five_pieces(self) -> None:
        """Pinned so the day someone changes the containment rule, this speaks."""
        measured = _plate_with_holes_inside_the_bore()("catia_measure", {})

        assert measured["solid_count"] == 5

    def test_the_volume_is_the_plate_plus_four_posts(self) -> None:
        """The arithmetic that proves what the five solids are: the bore is
        removed and the four little circles are *added* as posts, because they
        fell inside the hole where there was no material to cut."""
        measured = _plate_with_holes_inside_the_bore()("catia_measure", {})

        expected = (
            PLATE_X * PLATE_Y * THICK
            - math.pi * (BORE_D / 2) ** 2 * THICK
            + 4 * math.pi * (HOLE_D / 2) ** 2 * THICK
        )
        assert measured["volume_mm3"] == pytest.approx(expected, abs=1e-6)

    def test_the_mass_lands_inside_the_users_tolerance_anyway(self) -> None:
        """The whole reason this needed a guard rather than better arithmetic.

        2.4 kg was asked for with a 20 g tolerance. This wrong part weighs
        2.391 kg. Nothing about the number is a clue.
        """
        measured = _plate_with_holes_inside_the_bore()("catia_measure", {})

        assert abs(measured["mass_kg"] - 2.4) < 0.020

    def test_but_the_payload_now_says_it_is_in_pieces(self) -> None:
        measured = _plate_with_holes_inside_the_bore()("catia_measure", {})

        assert measured["in_pieces"] is True
        assert "5 separate solids" in measured["advisory"]

    def test_the_advisory_says_what_probably_went_wrong(self) -> None:
        """A flag the agent cannot act on is a flag it will ignore. The likely
        cause — a hole drawn inside another hole — is the actionable half."""
        measured = _plate_with_holes_inside_the_bore()("catia_measure", {})

        assert "hole" in measured["advisory"]


class TestAWholePartIsUnchanged:
    """The guard must cost a correct part nothing, or every payload asserted
    against it starts failing."""

    def test_a_plain_plate_carries_no_advisory(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "plain"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 20.0})

        measured = runner("catia_measure", {})

        assert measured["solid_count"] == 1
        assert "in_pieces" not in measured
        assert "advisory" not in measured

    def test_a_plate_with_a_real_bore_carries_no_advisory(self) -> None:
        """The part gate G1 got *right* on its first prompt — one solid, correct
        mass — must stay silent."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "bored"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 200.0, "height_mm": 150.0})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 60.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 10.0})

        measured = runner("catia_measure", {})

        assert measured["solid_count"] == 1
        assert "in_pieces" not in measured

    def test_an_empty_document_is_not_reported_as_in_pieces(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "empty"})

        measured = runner("catia_measure", {})

        assert "in_pieces" not in measured

    def test_two_deliberately_separate_solids_are_flagged_not_refused(self) -> None:
        """A multi-body design is legitimate. The rule is that it is never
        *silent*, not that it is forbidden — refusing it would break every part
        that means to be in more than one piece."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "two"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 10.0, "at": [-20.0, 0.0]})
        runner("catia_sketch_circle", {"sketch": "s", "diameter_mm": 10.0, "at": [20.0, 0.0]})
        built = runner("catia_pad", {"sketch": "s", "length_mm": 5.0})

        assert built["solid_count"] == 2
        measured = runner("catia_measure", {})
        assert measured["in_pieces"] is True
