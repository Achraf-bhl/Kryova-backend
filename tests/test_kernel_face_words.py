"""A bare face word means the same thing to every operation that takes a plane.

`top`, `bottom`, `front`, `back`, `left`, `right` are what an engineer says and,
measured at ladder Level 2 on 2026-09-09, **the first thing the model reaches
for**. They were added to `sketcher.resolve_support` that day, after the seat had
accepted `support="top"` for some time and the open kernel refused it.

**They were not added to `elements.plane_frame`, and that resolver's own docstring
calls itself "the one resolver every operation that takes 'which plane' goes
through … Before it, each had its own accept-list."** So for two days
`catia_sketch_create(support="top")` worked and `catia_hole_at(face="top")`
answered *"There is nothing called 'top' in this part"*.

**Measured at ladder Level 4 on 2026-09-11**, `qwen3.6:27b`, on the open kernel.
Asked for a bolt hole on the fixed end of a bracket, the agent tried
`face="left"`, was refused, called `catia_list_faces` to find out what the part
really had, tried `face="normal [-1, 0, 0]"` from what that returned, was refused
again, and spent seven further steps routing around it. Every refusal was correct
about its own accept-list and wrong about the product.

The table now lives in `elements` and `sketcher` reads it from there, so there is
one of it. These tests are the reason it cannot quietly become two again.

Offline: no seat, no database, no bridge.
"""

from __future__ import annotations

import pytest

from app.kernel import available
from app.kernel.errors import GeometryError
from app.kernel.occt import elements

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

FACE_WORDS = ("top", "bottom", "front", "back", "left", "right")


def _bar():  # type: ignore[no-untyped-def]
    """The Level 4 bracket: a flat bar 180 x 90 x 10."""
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "Bracket"})
    runner("catia_sketch_create", {"name": "S", "support": "XY"})
    runner("catia_sketch_rectangle", {"sketch": "S", "width_mm": 180, "height_mm": 90})
    runner("catia_pad", {"sketch": "S", "length_mm": 10})
    return runner


class TestThereIsOnlyOneTableOfFaceWords:
    def test_the_sketcher_reads_the_shared_one(self) -> None:
        """A second copy is how the two drifted apart in the first place."""
        from app.kernel.occt.operations import sketcher

        assert sketcher._BOUNDING_BOX_FACES is elements.BOUNDING_BOX_FACES

    def test_it_holds_the_six_words_an_engineer_says(self) -> None:
        assert set(elements.BOUNDING_BOX_FACES) == set(FACE_WORDS)


class TestEveryOperationTakingAPlaneAcceptsThem:
    @pytest.mark.parametrize("word", FACE_WORDS)
    def test_hole_at_accepts_each_word(self, word: str) -> None:
        """The exact call L4 was refused on, for all six words rather than the
        one that happened to come up."""
        built = _bar()("catia_hole_at", {"face": word, "at": [0, 0], "diameter_mm": 9,
                                         "depth_mm": 5})
        assert built["volume_mm3"] > 0

    def test_a_sketch_still_accepts_them(self) -> None:
        """The behaviour that already worked must not have moved."""
        drawn = _bar()("catia_sketch_create", {"name": "onTop", "support": "top"})
        assert drawn["support"] == "top"

    def test_a_plane_offset_accepts_them(self) -> None:
        made = _bar()("catia_plane_offset", {"reference": "top", "distance_mm": 25,
                                             "name": "above"})
        assert made["feature"] == "above"

    def test_a_mirror_accepts_them(self) -> None:
        assert _bar()("catia_mirror", {"plane": "right"})["volume_mm3"] > 0

    def test_a_symmetry_accepts_them(self) -> None:
        assert _bar()("catia_symmetry", {"reference": "top"})["volume_mm3"] > 0


class TestTheWordsStillMeanWhatTheySay:
    def test_top_and_bottom_are_the_two_ends_of_the_same_axis(self) -> None:
        """A word that resolved to the wrong end would build a part inside out,
        and every count and area would still look right."""
        runner = _bar()
        top = elements.plane_frame(runner.document, "top", tool="t").Location().Z()
        bottom = elements.plane_frame(runner.document, "bottom", tool="t").Location().Z()
        assert top == pytest.approx(10.0, abs=1e-6)
        assert bottom == pytest.approx(0.0, abs=1e-6)

    def test_left_and_right_are_the_two_ends_of_x(self) -> None:
        runner = _bar()
        left = elements.plane_frame(runner.document, "left", tool="t").Location().X()
        right = elements.plane_frame(runner.document, "right", tool="t").Location().X()
        assert left == pytest.approx(-90.0, abs=1e-6)
        assert right == pytest.approx(90.0, abs=1e-6)

    def test_a_word_is_refused_before_anything_is_built(self) -> None:
        """With no shape there is no bounding box, so the word cannot resolve —
        and saying so is better than resolving it against the origin."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Empty"})
        with pytest.raises(GeometryError):
            elements.plane_frame(runner.document, "top", tool="catia_hole_at")

    def test_something_that_is_not_a_word_still_says_what_the_part_holds(self) -> None:
        """The refusal L4 got for `normal [-1, 0, 0]` was the right refusal for
        the wrong input, and it must stay that way for genuine typos."""
        with pytest.raises(GeometryError) as caught:
            _bar()("catia_hole_at", {"face": "sideways", "at": [0, 0], "diameter_mm": 9,
                                     "depth_mm": 5})
        assert "sideways" in str(caught.value)
        assert "Pad.1" in str(caught.value), "it names what the part does hold"
