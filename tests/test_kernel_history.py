"""`catia_delete_feature` and `catia_feature_parents` on the open kernel (master plan E1.3).

The need was measured on 2026-09-11. The agent padded 200 mm instead of 10,
diagnosed the mistake itself, and had no way to take the pad back, so the part
was unrecoverable inside the conversation. A delete here is a rebuild from the
build log without the calls that made the feature. These tests pin what makes
that safe to offer: the part really loses the feature, nothing else moves, a
delete that would break a later call changes nothing, and names stay put.
"""

from __future__ import annotations

import pytest

from app.kernel.errors import GeometryError, OperationNotSupported

PLATE_MM3 = 100.0 * 60.0 * 10.0


def _runner():
    from app.kernel import OcctRunner

    return OcctRunner()


def _plate_with_a_boss(runner):
    """A 100x60x10 plate, then a 20x20x5 boss on a separate sketch."""
    runner("catia_new_part", {"name": "Plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner("catia_sketch_rectangle", {"sketch": "outline", "width_mm": 100.0, "height_mm": 60.0})
    runner("catia_pad", {"sketch": "outline", "length_mm": 10.0})
    runner("catia_sketch_create", {"support": "Pad.1#top", "name": "boss"})
    runner("catia_sketch_rectangle", {"sketch": "boss", "width_mm": 20.0, "height_mm": 20.0})
    runner("catia_pad", {"sketch": "boss", "length_mm": 5.0})
    return runner


def _volume(runner) -> float:
    return float(runner("catia_measure", {})["volume_mm3"])


def _three_blocks(runner):
    """Three separate 10 mm cubes along x, each padded from its own sketch."""
    runner("catia_new_part", {"name": "Blocks"})
    for index, x in enumerate((0.0, 50.0, 100.0), start=1):
        runner("catia_sketch_create", {"support": "XY", "name": f"s{index}"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": f"s{index}", "width_mm": 10.0, "height_mm": 10.0, "at": [x, 0.0]},
        )
        runner("catia_pad", {"sketch": f"s{index}", "length_mm": 10.0})
    return runner


class TestADeletedFeatureIsGone:
    def test_the_boss_comes_off_and_the_plate_is_what_is_left(self) -> None:
        runner = _plate_with_a_boss(_runner())

        result = runner("catia_delete_feature", {"feature": "Pad.2", "with_children": False})

        assert result["deleted"] == ["Pad.2"]
        assert result["volume_mm3"] == pytest.approx(PLATE_MM3)
        assert runner.document.feature_names() == ["Pad.1"]

    def test_the_wrong_pad_that_started_this_can_be_taken_back_and_redone(self) -> None:
        """The 2026-09-11 conversation: 200 mm padded where 10 was asked."""
        runner = _runner()
        runner("catia_new_part", {"name": "Plate"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner("catia_sketch_rectangle", {"sketch": "outline", "width_mm": 100.0, "height_mm": 60.0})
        runner("catia_pad", {"sketch": "outline", "length_mm": 200.0})

        runner("catia_delete_feature", {"feature": "Pad.1"})
        redone = runner("catia_pad", {"sketch": "outline", "length_mm": 10.0})

        assert redone["volume_mm3"] == pytest.approx(PLATE_MM3)

    def test_the_delete_is_not_itself_recorded(self) -> None:
        runner = _plate_with_a_boss(_runner())

        runner("catia_delete_feature", {"feature": "Pad.2"})

        tools = [entry.tool for entry in runner._context.journal]
        assert "catia_delete_feature" not in tools
        assert tools.count("catia_pad") == 1

    def test_a_parameter_set_afterwards_replays_the_part_without_it(self) -> None:
        runner = _plate_with_a_boss(_runner())
        runner("catia_delete_feature", {"feature": "Pad.2"})

        result = runner("catia_set_parameter", {"name": "Pad.1\\length_mm", "value": 20.0})

        assert result["volume_mm3"] == pytest.approx(2 * PLATE_MM3)


class TestWhatNamesItIsRefusedOrTakenWithIt:
    def test_a_feature_something_is_built_on_is_refused_with_the_dependents_named(self) -> None:
        runner = _plate_with_a_boss(_runner())

        with pytest.raises(GeometryError) as caught:
            runner("catia_delete_feature", {"feature": "Pad.1"})

        message = str(caught.value)
        assert "boss" in message and "Pad.2" in message
        assert "with_children" in message

    def test_and_the_refused_part_is_exactly_as_it_was(self) -> None:
        runner = _plate_with_a_boss(_runner())
        before = _volume(runner)

        with pytest.raises(GeometryError):
            runner("catia_delete_feature", {"feature": "Pad.1"})

        assert _volume(runner) == pytest.approx(before)
        assert runner.document.feature_names() == ["Pad.1", "Pad.2"]

    def test_with_children_takes_everything_that_named_it(self) -> None:
        runner = _plate_with_a_boss(_runner())

        result = runner("catia_delete_feature", {"feature": "Pad.1", "with_children": True})

        assert result["deleted"] == ["Pad.1", "boss", "Pad.2"]
        assert runner.document.feature_names() == []
        assert runner.document.sketch_names() == ["outline"]

    def test_a_sketch_and_what_was_padded_from_it_go_together(self) -> None:
        runner = _three_blocks(_runner())

        result = runner("catia_delete_feature", {"feature": "s2", "with_children": True})

        assert result["deleted"] == ["s2", "Pad.2"]
        assert result["volume_mm3"] == pytest.approx(2 * 1000.0)

    def test_a_sketch_with_nothing_built_on_it_goes_on_its_own(self) -> None:
        """What was drawn into a sketch is part of the sketch, not built on it, so
        it does not make the delete need with_children."""
        runner = _plate_with_a_boss(_runner())
        runner("catia_sketch_create", {"support": "XY", "name": "spare"})
        runner("catia_sketch_rectangle", {"sketch": "spare", "width_mm": 5.0, "height_mm": 5.0})

        result = runner("catia_delete_feature", {"feature": "spare"})

        assert result["deleted"] == ["spare"]
        assert runner.document.sketch_names() == ["boss", "outline"]

    def test_a_rename_goes_with_its_feature_and_does_not_block_the_delete(self) -> None:
        runner = _plate_with_a_boss(_runner())
        runner("catia_feature_rename", {"feature": "Pad.2", "name": "boss_body"})

        result = runner("catia_delete_feature", {"feature": "boss_body"})

        assert result["deleted"] == ["boss_body"], "reported by the name it has now"
        assert result["features"] == ["Pad.1"]
        assert "catia_feature_rename" not in [e.tool for e in runner._context.journal]


class TestTheRebuildMustStillBuild:
    def test_a_cut_that_only_cut_the_deleted_material_refuses_the_delete(self) -> None:
        """A pocket names its sketch, not the pad it cut. Remove the pad and the
        pocket cuts nothing, which the kernel refuses, so the delete is refused."""
        runner = _three_blocks(_runner())
        runner("catia_sketch_create", {"support": "XY", "name": "hole"})
        runner("catia_sketch_circle", {"sketch": "hole", "diameter_mm": 4.0, "at": [50.0, 0.0]})
        runner("catia_pocket", {"sketch": "hole", "through_all": True})
        before = _volume(runner)

        with pytest.raises(GeometryError) as caught:
            runner("catia_delete_feature", {"feature": "Pad.2"})

        assert "no longer builds" in str(caught.value)
        assert "catia_pocket" in str(caught.value)
        assert _volume(runner) == pytest.approx(before)


class TestNamesDoNotMove:
    def test_a_later_feature_keeps_its_number(self) -> None:
        runner = _three_blocks(_runner())

        runner("catia_delete_feature", {"feature": "Pad.2"})

        assert runner.document.feature_names() == ["Pad.1", "Pad.3"]

    def test_a_reference_to_a_later_feature_still_means_that_feature(self) -> None:
        """All three tops are at z = 10, so height cannot tell them apart; where the
        face sits along x can. Block three is the one centred at x = 100."""
        from app.kernel.occt import elements

        runner = _three_blocks(_runner())
        runner("catia_delete_feature", {"feature": "Pad.1"})

        top = elements.plane_frame(runner.document, "Pad.3#top", tool="test").Location()

        assert (top.X(), top.Z()) == (pytest.approx(100.0), pytest.approx(10.0))
        sketched = runner("catia_sketch_create", {"support": "Pad.3#top", "name": "on_c"})
        assert sketched["support"] == "Pad.3#top"

    def test_a_rebuild_that_would_renumber_a_feature_is_refused(self, monkeypatch) -> None:
        """The last line of defence behind the numbering. If the rebuilt part ever
        called `Pad.2` something else, every reference to it would move."""
        from app.kernel.occt.document import PartDocument

        monkeypatch.setattr(PartDocument, "continue_numbering", lambda *_args: None)
        runner = _three_blocks(_runner())

        with pytest.raises(GeometryError) as caught:
            runner("catia_delete_feature", {"feature": "Pad.1"})

        assert "would rename 'Pad.2'" in str(caught.value)
        assert runner.document.feature_names() == ["Pad.1", "Pad.2", "Pad.3"]

    def test_continuing_a_count_never_moves_it_backwards(self) -> None:
        """A rebuild asks for "at least this number". A counter already past it
        must stay put, or the next pad would reuse a name already in the part."""
        runner = _three_blocks(_runner())

        runner.document.continue_numbering("Pad", 1)
        runner("catia_sketch_create", {"support": "XY", "name": "s4"})
        runner("catia_sketch_rectangle", {"sketch": "s4", "width_mm": 10.0, "height_mm": 10.0, "at": [150.0, 0.0]})
        fresh = runner("catia_pad", {"sketch": "s4", "length_mm": 10.0})

        assert fresh["feature"] == "Pad.4"

    def test_a_deleted_number_is_not_handed_out_again(self) -> None:
        runner = _three_blocks(_runner())
        runner("catia_delete_feature", {"feature": "Pad.3"})

        runner("catia_sketch_create", {"support": "XY", "name": "s4"})
        runner("catia_sketch_rectangle", {"sketch": "s4", "width_mm": 10.0, "height_mm": 10.0, "at": [150.0, 0.0]})
        fresh = runner("catia_pad", {"sketch": "s4", "length_mm": 10.0})

        assert fresh["feature"] == "Pad.4"


class TestItRefusesWhatItCannotDo:
    def test_an_unknown_name_lists_what_there_is(self) -> None:
        runner = _plate_with_a_boss(_runner())

        with pytest.raises(GeometryError) as caught:
            runner("catia_delete_feature", {"feature": "Pad.9"})

        assert "Pad.1" in str(caught.value) and "boss" in str(caught.value)

    def test_the_part_itself_is_not_a_feature(self) -> None:
        runner = _plate_with_a_boss(_runner())

        with pytest.raises(GeometryError) as caught:
            runner("catia_delete_feature", {"feature": "Plate"})

        assert "is the part itself" in str(caught.value)
        assert _volume(runner) == pytest.approx(PLATE_MM3 + 20.0 * 20.0 * 5.0)

    def test_a_body_is_refused_by_name_because_its_features_do_not_name_it(self) -> None:
        runner = _plate_with_a_boss(_runner())

        with pytest.raises(OperationNotSupported) as caught:
            runner("catia_delete_feature", {"feature": "PartBody"})

        assert "do not name it" in str(caught.value)

    def test_two_sketches_sharing_a_name_are_refused_rather_than_guessed(self) -> None:
        runner = _runner()
        runner("catia_new_part", {"name": "Twice"})
        runner("catia_sketch_create", {"support": "XY"})
        runner("catia_sketch_rectangle", {"sketch": "sketch", "width_mm": 10.0, "height_mm": 10.0})
        runner("catia_sketch_create", {"support": "YZ"})

        with pytest.raises(GeometryError) as caught:
            runner("catia_delete_feature", {"feature": "sketch"})

        assert "does not say which one" in str(caught.value)


class TestTheParentsAreReadFromTheBuildLog:
    def test_the_boss_is_built_on_its_sketch(self) -> None:
        runner = _plate_with_a_boss(_runner())

        result = runner("catia_feature_parents", {"feature": "Pad.2"})

        assert result["parents"] == ["boss"]
        assert result["children"] == []

    def test_the_plate_has_the_boss_sketch_as_a_child_and_the_boss_one_level_down(self) -> None:
        runner = _plate_with_a_boss(_runner())

        one = runner("catia_feature_parents", {"feature": "Pad.1"})
        two = runner("catia_feature_parents", {"feature": "Pad.1", "depth": 2})

        assert [child["feature"] for child in one["children"]] == ["boss"]
        assert [(c["feature"], c["depth"]) for c in two["children"]] == [("boss", 1), ("Pad.2", 2)]

    def test_it_says_what_it_cannot_see(self) -> None:
        runner = _plate_with_a_boss(_runner())

        note = runner("catia_feature_parents", {"feature": "Pad.1"})["note"]

        assert "does not name" in note

    def test_it_is_read_only(self) -> None:
        runner = _plate_with_a_boss(_runner())
        length = len(runner._context.journal)

        runner("catia_feature_parents", {"feature": "Pad.1"})

        assert len(runner._context.journal) == length
