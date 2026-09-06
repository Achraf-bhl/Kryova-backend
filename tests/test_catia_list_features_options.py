"""`catia_list_features` honours the three options its schema advertises.

`body`, `kind` and `include_sketches` sat on `KNOWN_NARROWER` -- the schema
offered them, the bridge did not implement them, and the model was refused in
words. Legible, but this is the tool whose own description calls itself "the
first call to make on any document you did not just build yourself", so it is
the entry on that list a real run is most likely to hit.

It hit one. Ladder prompt H2, 2026-09-06: the agent had drawn a rectangle *and*
two circles into a single sketch, which is not something a pad can extrude. It
tried `catia_select("Rectangle.1")`, was correctly told there is no such
feature, and then reached for `catia_list_features(include_sketches=True)` --
precisely the right next move -- and was refused. It never found out what was in
the sketch, retried the pad twice against the same profile, and the run ended
with `Corps principal` in an error state and nothing built.

So the gap is closed rather than carried. These run against the mock: no CATIA,
no seat.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.catia.tool_specs import get_spec
from app.catia.validation import validate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402


@pytest.fixture
def catia(tmp_path: Path) -> MockCatia:
    """A plate with a pad and a pocket, built from two sketches."""
    catia = MockCatia(tmp_path / "catia")
    catia.new_part(name="plate")
    catia.sketch_rectangle(plane="XY", width_mm=120, height_mm=80)
    catia.pad(sketch="Sketch.1", length_mm=15)
    catia.sketch_circle(plane="XY", diameter_mm=30)
    catia.pocket(sketch="Sketch.2", through_all=True)
    return catia


def test_the_schema_really_does_advertise_all_three() -> None:
    """If the schema drops one, the rest of this file is testing a ghost."""
    spec = get_spec("catia_list_features")
    assert spec is not None
    for option in ("body", "kind", "include_sketches"):
        assert option in spec.parameters["properties"], option


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"include_sketches": False},
        {"kind": "Pad"},
        {"body": "PartBody"},
        {"body": "PartBody", "kind": "Pocket", "include_sketches": True},
    ],
)
def test_every_advertised_form_passes_validation(arguments: dict[str, object]) -> None:
    spec = get_spec("catia_list_features")
    assert spec is not None
    validate(arguments, spec.parameters)


class TestIncludeSketches:
    def test_sketches_are_listed_by_default(self, catia: MockCatia) -> None:
        """The default the schema states, and the thing H2 needed."""
        names = [f["name"] for f in catia.list_features()["features"]]
        assert names == ["Sketch.1", "Pad.1", "Sketch.2", "Pocket.1"]

    def test_they_can_be_left_out(self, catia: MockCatia) -> None:
        rows = catia.list_features(include_sketches=False)["features"]
        assert [f["name"] for f in rows] == ["Pad.1", "Pocket.1"]
        assert all(f["type"] != "Sketch" for f in rows)

    def test_a_sketch_row_says_how_many_elements_it_holds(self, catia: MockCatia) -> None:
        """The field that answers H2's actual question.

        A pad needs one profile. `Sketch.1` tells the agent nothing about
        whether it has one; `Sketch.1, 3 elements` tells it the pad is going to
        fail and why, before it fails with `La methode Update a echoue`.
        """
        rows = catia.list_features()["features"]
        sketches = [f for f in rows if f["type"] == "Sketch"]
        assert sketches
        assert all("elements" in f for f in sketches)


class TestKind:
    def test_it_filters_to_one_type(self, catia: MockCatia) -> None:
        rows = catia.list_features(kind="Pad")["features"]
        assert [f["name"] for f in rows] == ["Pad.1"]

    def test_it_is_case_insensitive(self, catia: MockCatia) -> None:
        """The model types what the user said, not what CATIA capitalises."""
        assert catia.list_features(kind="pocket")["features"] == (
            catia.list_features(kind="Pocket")["features"]
        )

    def test_a_type_that_is_not_there_says_what_is(self, catia: MockCatia) -> None:
        """An empty list is a second round trip. Answer the next question now."""
        result = catia.list_features(kind="Chamfer")
        assert result["features"] == []
        assert "Pad" in result["note"] and "Pocket" in result["note"]

    def test_the_filter_still_respects_include_sketches(self, catia: MockCatia) -> None:
        assert catia.list_features(kind="Sketch", include_sketches=False)["features"] == []


class TestBody:
    def test_the_part_s_own_body_is_accepted(self, catia: MockCatia) -> None:
        assert catia.list_features(body="PartBody")["features"]

    def test_an_invented_body_is_refused_and_names_what_exists(self, catia: MockCatia) -> None:
        """Silently ignoring it would answer about a different body than asked."""
        with pytest.raises(CatiaOperationError) as caught:
            catia.list_features(body="Body.7")
        assert "Body.7" in str(caught.value)
        assert "PartBody" in str(caught.value)


def test_closing_the_gap_changed_nothing_a_mutation_reports(catia: MockCatia) -> None:
    """Every building tool returns `features` too, and those must not move.

    `_feature_names` gained a parameter, and a parameter with a default is
    exactly how the *other* callers of a function get changed by accident. Each
    backend's default was chosen to be the no-op for its own storage -- True in
    the mock, which has always kept sketches in one ordered list, and False on
    the COM side, whose `Body.Shapes` never held them -- so both report what
    they reported before.

    A silently reshaped mutation result is a bad failure: nothing errors, every
    tool still says ok, and the agent's picture of the part quietly stops
    matching the seat's.
    """
    from_mutation = catia.update()["features"]
    from_the_tool = catia.list_features()["features"]
    assert from_mutation == from_the_tool
    assert [f["name"] for f in from_mutation] == ["Sketch.1", "Pad.1", "Sketch.2", "Pocket.1"]
