"""`catia_list_features` honours its three advertised options **on the open
kernel too**.

`tests/test_catia_list_features_options.py` closed this gap on the CATIA side on
2026-09-06, after ladder prompt H2: the agent had drawn a rectangle and two
circles into one sketch, could not pad it, reached for
`catia_list_features(include_sketches=True)` -- precisely the right next move --
and was refused. **The open kernel was never given the same treatment.** Its
handler read none of `body`, `kind` or `include_sketches` and returned solid
features only, so the identical failure was still there, one backend over.

**It was measured on 2026-09-11, ladder L2, `qwen3.6:27b`, `GEOMETRY_BACKEND=occt`.**
A rectangle had been drawn on sketch `'sketch'` (`profiles: 1`, `ok`), a pad on
it had just been turned back, and the model called this tool to find out what was
really in the document. It answered `{"features": [], "detail": []}` -- true of
solid features, and read, correctly, as *"the part is empty"*. The model said so
in as many words, threw the sketch away and rebuilt from scratch, six steps
later. An empty answer to a question about a document that is not empty is worse
than a refusal, because it is believed.

That is the divergence class `CLAUDE.md` records from the other direction on
2026-09-09, when `catia_sketch_create` was the last operation still holding a
private accept-list and the seat accepted a `support="top"` the open kernel
refused. A capability closed on one backend and left open on the other is a
product that behaves differently depending on a setting nobody in the
conversation can see.

Offline: no seat, no database, no bridge.
"""

from __future__ import annotations

import pytest

from app.catia.tool_specs import get_spec
from app.kernel import available
from app.kernel.errors import GeometryError

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)


@pytest.fixture
def runner():  # type: ignore[no-untyped-def]
    """A plate with a pad and a pocket, built from two sketches -- and one more
    sketch drawn but never consumed, which is the state H2 and L2 were both in."""
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "plate"})
    runner("catia_sketch_create", {"name": "Outline", "support": "XY"})
    runner("catia_sketch_rectangle", {"sketch": "Outline", "width_mm": 120, "height_mm": 80})
    runner("catia_pad", {"sketch": "Outline", "length_mm": 15})
    runner("catia_sketch_create", {"name": "Bore", "support": "XY"})
    runner("catia_sketch_circle", {"sketch": "Bore", "diameter_mm": 30})
    runner("catia_pocket", {"sketch": "Bore", "through_all": True})
    # Drawn, closed, and not built from -- the sketch L2's pad was refused for.
    runner("catia_sketch_create", {"name": "Spare", "support": "XY"})
    runner("catia_sketch_rectangle", {"sketch": "Spare", "width_mm": 10, "height_mm": 10})
    return runner


def test_the_schema_really_does_advertise_all_three() -> None:
    """If the schema drops one, the rest of this file is testing a ghost."""
    spec = get_spec("catia_list_features")
    assert spec is not None
    for option in ("body", "kind", "include_sketches"):
        assert option in spec.parameters["properties"], option


class TestIncludeSketches:
    def test_sketches_are_listed_by_default(self, runner) -> None:  # type: ignore[no-untyped-def]
        """The default the schema states, and the thing L2 needed.

        Before the fix there was no `sketches` key at all and nothing anywhere in
        the answer mentioned a sketch.
        """
        names = [one["name"] for one in runner("catia_list_features", {})["sketches"]]
        assert names == ["Bore", "Outline", "Spare"]

    def test_they_arrive_beside_the_features_and_not_among_them(self, runner) -> None:  # type: ignore[no-untyped-def]
        """`features` is `document.feature_names()` and is what every *mutating*
        operation returns too. Folding sketches into it would leave the listing
        and the build results disagreeing about what the part contains — the
        failure `test_closing_the_gap_changed_nothing_a_mutation_reports` guards
        against on the CATIA side. A sketch is a drawing, not a feature."""
        answer = runner("catia_list_features", {})
        assert answer["features"] == ["Pad.1", "Pocket.1"]
        assert answer["sketches"]

    def test_asking_for_them_explicitly_is_the_same_answer(self, runner) -> None:  # type: ignore[no-untyped-def]
        """L2 sent `include_sketches: true` and the argument was not read at all,
        so the explicit form is worth its own assertion."""
        assert (
            runner("catia_list_features", {"include_sketches": True})["sketches"]
            == runner("catia_list_features", {})["sketches"]
        )

    def test_they_can_be_left_out(self, runner) -> None:  # type: ignore[no-untyped-def]
        answer = runner("catia_list_features", {"include_sketches": False})
        assert "sketches" not in answer
        assert answer["features"] == ["Pad.1", "Pocket.1"]

    def test_a_sketch_row_says_whether_anything_can_be_built_from_it(self, runner) -> None:  # type: ignore[no-untyped-def]
        """The field that answers L2's actual question.

        A pad needs one closed profile. `Spare` tells the agent nothing about
        whether it has one; `profiles: 1` tells it the pad will work, and
        `profiles: 0` tells it why the pad was refused -- before it is refused
        a second time and concludes the document is empty.
        """
        sketches = runner("catia_list_features", {})["sketches"]
        assert sketches
        assert all("elements" in one and "profiles" in one for one in sketches)
        spare = next(one for one in sketches if one["name"] == "Spare")
        assert spare["profiles"] == 1
        assert spare["can_be_built_from"] is True

    def test_an_empty_sketch_says_it_cannot_be_built_from(self, runner) -> None:  # type: ignore[no-untyped-def]
        """The exact state L2's pad was refused against."""
        runner("catia_sketch_create", {"name": "Empty", "support": "XY"})
        sketches = runner("catia_list_features", {})["sketches"]
        empty = next(one for one in sketches if one["name"] == "Empty")
        assert empty["profiles"] == 0
        assert empty["can_be_built_from"] is False


class TestKind:
    def test_it_filters_to_one_type(self, runner) -> None:  # type: ignore[no-untyped-def]
        assert runner("catia_list_features", {"kind": "Pad"})["features"] == ["Pad.1"]

    def test_it_is_case_insensitive(self, runner) -> None:  # type: ignore[no-untyped-def]
        """The model types what the user said, not what CATIA capitalises."""
        assert (
            runner("catia_list_features", {"kind": "pocket"})["features"]
            == runner("catia_list_features", {"kind": "Pocket"})["features"]
        )

    def test_a_type_that_is_not_there_says_what_is(self, runner) -> None:  # type: ignore[no-untyped-def]
        """An empty list is a second round trip. Answer the next question now."""
        result = runner("catia_list_features", {"kind": "Chamfer"})
        assert result["features"] == []
        assert "Pad" in result["note"] and "Pocket" in result["note"]

    def test_a_sketch_has_no_feature_type_to_filter_on(self, runner) -> None:  # type: ignore[no-untyped-def]
        """`kind` filters features, and a sketch is not one. Asking for kind
        'Sketch' therefore names what types *are* present rather than returning
        the sketches under a heading that would make them look like material."""
        answer = runner("catia_list_features", {"kind": "Sketch"})
        assert answer["features"] == []
        assert "Pad" in answer["note"]
        assert answer["sketches"], "they are still reported, just not as features"


class TestBody:
    def test_the_part_s_own_body_is_accepted(self, runner) -> None:  # type: ignore[no-untyped-def]
        assert runner("catia_list_features", {"body": "PartBody"})["features"]

    def test_an_invented_body_is_refused_and_names_what_exists(self, runner) -> None:  # type: ignore[no-untyped-def]
        """Silently ignoring it would answer about a different body than asked."""
        with pytest.raises(GeometryError) as caught:
            runner("catia_list_features", {"body": "Body.7"})
        assert "Body.7" in str(caught.value)
        assert "PartBody" in str(caught.value)
