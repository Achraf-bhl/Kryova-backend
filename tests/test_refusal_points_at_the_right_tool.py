"""A refusal that caused the wrong recovery — gate G1, 2026-09-06.

`CLAUDE.md` says an over-refusal is not safe, because *"the agent's recovery from
a refusal is to try something else, so it becomes a wrongly built part."* This is
the same failure one step further in: not a refusal that was wrong, but a refusal
whose **message answered a different question from the one being asked**.

Measured through the real chatbot at gate G1. Told to adjust a plate's thickness
until it weighed 2.4 kg, the agent called

    catia_pad {"feature": "Pad.1", "length_mm": 11}

which is unmistakably *"make Pad.1 eleven millimetres"*. It was told:

    catia_pad needs a sketch to build from, and none was named.

So it named one. It called `catia_feature_rename` to move `Pad.1` out of the way
and **padded the same sketch a second time**, and the document ended carrying two
pads. Every call after the refusal was a correct response to the sentence it had
been given. The sentence was the defect.

`catia_set_parameter` exists precisely for this and was built for it on
2026-09-05. The agent has not used it in three sessions running. The message now
names it — with the parameter spelled exactly as `catia_list_parameters` spells
it, because a name the model cannot type is a tool it cannot call, which was
itself a measured defect (`454d780`).

The test that matters here is the last one: the advice the message gives must
actually work when followed literally.
"""

from __future__ import annotations

import pytest

from app.kernel.errors import GeometryError


def _plate():
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "s"})
    runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0, "height_mm": 40.0})
    runner("catia_pad", {"sketch": "s", "length_mm": 10.0})
    runner("catia_set_material", {"material": "steel-1018"})
    return runner


class TestResizingAnExistingFeature:
    def test_it_is_still_refused(self) -> None:
        """`catia_pad` does not resize. That part was always right."""
        runner = _plate()

        with pytest.raises(GeometryError):
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})

    def test_the_message_names_catia_set_parameter(self) -> None:
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})

        assert "catia_set_parameter" in str(refused.value)

    def test_it_does_not_ask_for_a_sketch(self) -> None:
        """The sentence that caused the second pad. Asking for a sketch here is
        answering a question nobody asked."""
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})

        assert "needs a sketch to build from" not in str(refused.value)

    def test_it_warns_against_the_recovery_the_agent_actually_chose(self) -> None:
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})

        assert "two features" in str(refused.value)

    def test_the_parameter_name_it_offers_is_the_one_that_works(self) -> None:
        """The whole point. A message that names a tool but spells its argument
        wrong is worse than one that says nothing, because the agent spends its
        next call proving the advice was useless.
        """
        runner = _plate()
        before = runner("catia_measure", {})["mass_kg"]

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})

        offered = str(refused.value).split("name='")[1].split("'")[0]
        runner("catia_set_parameter", {"name": offered, "value": 11.0})
        after = runner("catia_measure", {})

        assert after["bounding_box_mm"]["size"][2] == pytest.approx(11.0, abs=1e-6)
        assert after["mass_kg"] > before
        assert after["features"] == ["Pad.1"], "the part must have one pad, not two"

    def test_the_dimension_named_is_the_one_the_caller_supplied(self) -> None:
        """Not hard-coded to `length_mm`. A pocket is sized by its depth and a
        shaft by its angle, and naming the wrong argument would send the agent
        to a parameter that does not exist.

        Where the caller's argument genuinely does not belong to that feature —
        a pocket's `depth_mm` against a pad — the name offered will not resolve,
        and that is handled rather than hidden: `catia_set_parameter` answers
        with the list of parameters the part really has, which is the better
        recovery from a confused call than guessing what was meant.
        """
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pocket", {"feature": "Pad.1", "depth_mm": 3.0})

        assert "depth_mm" in str(refused.value)
        assert "length_mm" not in str(refused.value)

    def test_a_name_that_does_not_resolve_is_answered_with_the_real_ones(self) -> None:
        """The second half of the case above, end to end."""
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pocket", {"feature": "Pad.1", "depth_mm": 3.0})
        offered = str(refused.value).split("name='")[1].split("'")[0]

        with pytest.raises(GeometryError) as second:
            runner("catia_set_parameter", {"name": offered, "value": 3.0})

        assert "length_mm" in str(second.value)


class TestTheOtherTwoCases:
    def test_an_unknown_feature_lists_what_there_is(self) -> None:
        """A typo and a resize are different mistakes and get different help."""
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Padd.1", "length_mm": 11.0})

        message = str(refused.value)
        assert "not a feature of this part" in message
        assert "Pad.1" in message

    def test_no_feature_at_all_keeps_the_original_message(self) -> None:
        """Someone who simply forgot the sketch should be told about the sketch,
        which is what the message always said and is right for that case."""
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"length_mm": 11.0})

        assert "needs a sketch to build from" in str(refused.value)
        assert "catia_set_parameter" not in str(refused.value)

    def test_building_normally_is_untouched(self) -> None:
        """A boss that protrudes, not one buried in the plate.

        This test originally padded the circle 5 mm into a 10 mm plate, which
        adds no material at all — and the no-op guard added later caught it
        immediately, which is the guard earning its keep on the day it landed.
        15 mm stands 5 mm proud of the plate and is a real second feature.
        """
        runner = _plate()
        runner("catia_sketch_create", {"support": "XY", "name": "second"})
        runner("catia_sketch_circle", {"sketch": "second", "diameter_mm": 10.0})

        built = runner("catia_pad", {"sketch": "second", "length_mm": 15.0})

        assert built["has_solid"] is True
        assert built["volume_mm3"] > 60.0 * 40.0 * 10.0


class TestTheAdviceIsCompleteEnoughToFollow:
    """`catia_set_parameter` requires `unit`, and requires it for a real reason —
    a parameter is typed, and setting a length in degrees is a silent no-op that
    leaves the model looking unchanged with nothing to explain why.

    Measured at gate G1: told to use the tool and given the name but not the
    unit, the agent called it without one, was refused, and only got it right on
    its third attempt. Advice that is *nearly* complete costs a call, and on a
    model this size a wasted call is a minute.
    """

    def test_the_message_carries_the_unit_as_well_as_the_name(self) -> None:
        runner = _plate()

        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})

        assert "unit='mm'" in str(refused.value)

    def test_the_whole_suggested_call_passes_dispatch_validation(self) -> None:
        """The strongest form of this test: take the name and the unit out of the
        message and put them through the same validator a real tool call meets.
        A message that suggests a call the validator rejects is worse than one
        that suggests nothing.
        """
        import re

        from app.catia.dispatch import validate

        runner = _plate()
        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})
        message = str(refused.value)

        name = re.search(r"name='([^']+)'", message).group(1)
        unit = re.search(r"unit='([^']*)'", message).group(1)

        validate("catia_set_parameter", {"name": name, "value": 11.0, "unit": unit})

    def test_and_then_actually_resizes_the_part(self) -> None:
        import re

        runner = _plate()
        with pytest.raises(GeometryError) as refused:
            runner("catia_pad", {"feature": "Pad.1", "length_mm": 11.0})
        message = str(refused.value)

        runner(
            "catia_set_parameter",
            {
                "name": re.search(r"name='([^']+)'", message).group(1),
                "value": 11.0,
                "unit": re.search(r"unit='([^']*)'", message).group(1),
            },
        )

        measured = runner("catia_measure", {})
        assert measured["bounding_box_mm"]["size"][2] == pytest.approx(11.0, abs=1e-5)
        assert measured["features"] == ["Pad.1"]
