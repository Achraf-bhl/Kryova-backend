"""A profile outline is one call, not one call per line.

Measured on the seat, 2026-09-06, ladder prompt PRO1 -- the bench arbor press,
the first level-4 prompt. The agent never reached the assembly, never padded
anything, and never got past the first part. It spent all twenty rounds here:

    CATIA: sketch line       812 ms
    CATIA: sketch line       995 ms
    CATIA: sketch line       667 ms
    CATIA: sketch line       794 ms
    CATIA: sketch line       998 ms
    CATIA: sketch line       672 ms
    CATIA: sketch dimension  760 ms
    CATIA: sketch circle    1129 ms
    CATIA: sketch dimension  761 ms
    CATIA: sketch dimension  782 ms

Every call succeeded. A C-frame is eight or ten segments, and at one round each
a twenty-round turn is gone before the profile closes.

`catia_sketch_polyline` takes the whole list of points and draws the contour in
one call. It has existed the whole time. Two things sent the agent the other
way, and both are ours:

* `catia_sketch_line`'s own summary said **"Chain these to build an open or
  closed contour"** -- an instruction to do exactly what cost the run;
* `catia_sketch_polyline` was not in `CORE_TOOLS`, the floor that is never
  withheld, so on a turn where retrieval narrowed the offer it was not
  necessarily there to be found.

This is the third time the same shape of defect has been measured on this
ladder -- `draft_load_case` reachable only from the web form, `through_all`
present but never reached for -- and the pattern is worth naming: the
capability is in the product, and nothing points at it.

Offline: specs and sets.
"""

from __future__ import annotations

import pytest

from app.ai.tool_retrieval import CORE_TOOLS
from app.catia.tool_specs import CATIA_TOOL_SPECS

SPECS = {spec.name: spec for spec in CATIA_TOOL_SPECS}


class TestThePolylineIsReachable:
    def test_it_is_in_the_floor_that_is_never_withheld(self) -> None:
        assert "catia_sketch_polyline" in CORE_TOOLS

    def test_it_takes_the_whole_profile(self) -> None:
        properties = SPECS["catia_sketch_polyline"].parameters["properties"]
        assert "points" in properties
        assert "closed" in properties
        assert properties["points"]["type"] == "array"

    def test_the_core_set_stayed_small(self) -> None:
        """The floor is the shortest list a part can be built with. A rule
        broad enough to re-offer the registry gives back the problem 16.1
        exists to solve."""
        assert len(CORE_TOOLS) <= 18


class TestTheToolsSayWhichIsWhich:
    def test_the_line_no_longer_teaches_chaining(self) -> None:
        """The sentence that cost the run."""
        assert "Chain these" not in SPECS["catia_sketch_line"].description

    def test_the_line_points_at_the_polyline(self) -> None:
        summary = SPECS["catia_sketch_line"].description
        assert "catia_sketch_polyline" in summary
        assert "one call per segment" in summary

    def test_the_line_still_has_a_use_and_names_it(self) -> None:
        """Refusing to describe a legitimate use would be an over-correction:
        an axis or a single added edge really is one line."""
        summary = SPECS["catia_sketch_line"].description
        assert "one line" in summary

    def test_the_polyline_says_it_is_the_way_to_draw_a_profile(self) -> None:
        summary = SPECS["catia_sketch_polyline"].description
        # The comparison is the point, not the phrase "one call" on its own:
        # what the model has to read is that the alternative costs a round a
        # segment.
        assert "rather than one per segment" in summary
        for shape in ("C-frame", "bracket", "lever"):
            assert shape in summary

    def test_the_polyline_prefers_closed_over_a_repeated_point(self) -> None:
        """A repeated first point is easy to get a fraction wrong, and a
        profile that does not close exactly is refused by the pad."""
        assert "closed" in SPECS["catia_sketch_polyline"].description

    @pytest.mark.parametrize("name", ["catia_sketch_line", "catia_sketch_polyline"])
    def test_neither_description_names_a_tool_that_does_not_exist(self, name: str) -> None:
        import re

        named = set(re.findall(r"\bcatia_[a-z_]+\b", SPECS[name].description))
        assert named <= set(SPECS)
