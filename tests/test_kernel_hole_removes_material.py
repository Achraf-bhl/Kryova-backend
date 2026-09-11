"""A hole that removes nothing is not a hole.

`features.py` has refused a pad that adds nothing and a pocket that removes
nothing since gate G1 on 2026-09-06 — *"an operation that reports success while
achieving nothing ... it is refused rather than noted, because unlike an unused
sketch there is no reading under which a caller meant it. A feature that changes
no material is not a feature."*

**Holes never called it**, and holes are the family most likely to land off the
part: `catia_hole` places by a named spot on a bounding-box face and
`catia_hole_at` by explicit coordinates, so an arithmetic slip puts the cutter in
mid-air. `BRepAlgoAPI_Cut` succeeds when the tool and target do not overlap — the
answer is the target, unchanged, and `IsDone()` is true — so nothing anywhere
said the hole had missed.

Measured 2026-09-11 on a 180 x 90 x 10 plate: drilling the same hole twice gave
`HoleAt.2`, and drilling at `[500, 500]`, entirely beside the part, gave
`HoleAt.3`. Both `ok`, both 0 mm3 removed, both with a feature name and a full
set of `measured` provenance.

**Found at ladder Level 4 the same day.** The agent placed its second bolt hole
on top of its first, was told it had succeeded, and had to work out from the
volume that it had not — *"I notice the second hole placed at the same location
as the first. The volume didn't decrease, so it didn't actually cut material."*
It then spent seven steps recovering. The product should have said so.

Offline: no seat, no database, no bridge.
"""

from __future__ import annotations

import pytest

from app.kernel import available
from app.kernel.errors import GeometryError

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)


def _plate():  # type: ignore[no-untyped-def]
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "Bracket"})
    runner("catia_sketch_create", {"name": "S", "support": "XY"})
    runner("catia_sketch_rectangle", {"sketch": "S", "width_mm": 180, "height_mm": 90})
    runner("catia_pad", {"sketch": "S", "length_mm": 10})
    return runner


class TestAHoleThatCutsNothingIsRefused:
    def test_the_same_hole_twice_is_refused(self) -> None:
        """The Level 4 case exactly."""
        runner = _plate()
        runner("catia_hole_at", {"face": "top", "at": [0, 0], "diameter_mm": 9,
                                 "through_all": True})
        with pytest.raises(GeometryError, match="removed no material"):
            runner("catia_hole_at", {"face": "top", "at": [0, 0], "diameter_mm": 9,
                                     "through_all": True})

    def test_a_hole_beside_the_part_is_refused(self) -> None:
        """A bolt circle wider than the part puts every hole in the air, and this
        is the failure gate G1 measured for pockets."""
        with pytest.raises(GeometryError, match="removed no material"):
            _plate()("catia_hole_at", {"face": "top", "at": [500, 500], "diameter_mm": 9,
                                       "through_all": True})

    def test_catia_hole_is_guarded_too(self) -> None:
        """Both entry points go through `_drill`, and a guard on one of two doors
        is not a guard."""
        runner = _plate()
        runner("catia_hole", {"face": "top", "position": "center", "diameter_mm": 9,
                              "through_all": True})
        with pytest.raises(GeometryError, match="removed no material"):
            runner("catia_hole", {"face": "top", "position": "center", "diameter_mm": 9,
                                  "through_all": True})

    def test_a_real_hole_still_drills(self) -> None:
        """The guard must not cost a hole that does something — over-refusal is
        its own failure mode."""
        runner = _plate()
        before = runner("catia_measure", {})["volume_mm3"]
        after = runner("catia_hole_at", {"face": "top", "at": [40, 20], "diameter_mm": 9,
                                         "through_all": True})["volume_mm3"]
        assert after < before
        assert after == pytest.approx(before - 3.14159265 * 4.5**2 * 10, rel=1e-3)

    def test_two_holes_in_different_places_are_both_kept(self) -> None:
        runner = _plate()
        runner("catia_hole_at", {"face": "top", "at": [40, 20], "diameter_mm": 9,
                                 "through_all": True})
        built = runner("catia_hole_at", {"face": "top", "at": [-40, 20], "diameter_mm": 9,
                                         "through_all": True})
        assert built["feature"]


class TestTheRefusalFitsTheOperation:
    def test_a_hole_is_not_told_to_pass_reversed_true(self) -> None:
        """The pocket's advice names a sketch and a `reversed` argument. A hole is
        positioned, not sketched, and has neither — so the wrong half of a shared
        message would send the caller looking for an argument that does not
        exist. Worse than no advice."""
        runner = _plate()
        runner("catia_hole_at", {"face": "top", "at": [0, 0], "diameter_mm": 9,
                                 "through_all": True})
        with pytest.raises(GeometryError) as caught:
            runner("catia_hole_at", {"face": "top", "at": [0, 0], "diameter_mm": 9,
                                     "through_all": True})
        message = str(caught.value)
        assert "reversed" not in message
        assert "sketch" not in message.lower()
        assert "catia_list_features" in message, "it says how to find what is already there"

    def test_a_pocket_still_gets_the_sketch_advice(self) -> None:
        """The original remedy must not have been lost in the move."""
        runner = _plate()
        runner("catia_sketch_create", {"name": "P", "support": "XY"})
        runner("catia_sketch_circle", {"sketch": "P", "diameter_mm": 10, "at": [900, 900]})
        with pytest.raises(GeometryError) as caught:
            runner("catia_pocket", {"sketch": "P", "depth_mm": 5})
        assert "reversed: true" in str(caught.value)
