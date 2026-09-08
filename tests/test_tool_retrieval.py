"""Choosing which tools to show — master plan 16.1.

Offline: the registry is the corpus and it is already in memory, so there is no
index to build and nothing to keep in step.

The property most of these defend is the one that makes retrieval safe at all:
**narrowing the offer never narrows what can be called.** Everything else here is
a quality question, and a quality regression costs a turn. That one would cost a
capability, silently.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.tool_retrieval import (
    CORE_TOOLS,
    DEFAULT_LIMIT,
    describe_selection,
    score_tool,
    select_tool_names,
)


def _spec(name: str, description: str = "") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=description)


#: A stand-in registry with the shape of the real one: a core, some Part Design,
#: some surfacing, some wireframe, some drafting.
REGISTRY = [
    _spec("catia_new_part", "Create an empty CATPart and bind it to the conversation."),
    _spec("catia_sketch_create", "Create an empty sketch on a named plane."),
    _spec("catia_sketch_rectangle", "Draw a centred rectangle in a sketch."),
    _spec("catia_sketch_circle", "Draw a centred circle in a sketch."),
    _spec("catia_pad", "Extrude a sketch by a length to make material."),
    _spec("catia_pocket", "Cut a sketch through the part or to a depth."),
    _spec("catia_measure", "Mass, volume, bounding box and centre of gravity."),
    _spec("catia_capture_view", "Screenshot the part from a standard viewpoint."),
    _spec("catia_list_features", "The feature tree, in build order."),
    _spec("catia_update", "Force a part update or rebuild."),
    _spec("catia_open_document", "Reopen the conversation's document."),
    _spec("catia_status", "Is a bridge connected?"),
    _spec("catia_fillet", "Round edges of the part by a group name."),
    _spec("catia_chamfer", "Bevel edges of the part by a length."),
    _spec("catia_hole", "Hole on a face at a named position."),
    _spec("catia_pattern_circular", "Repeat a feature around an axis."),
    _spec("catia_pattern_rectangular", "Repeat a feature on a grid."),
    _spec("catia_draft", "Apply a draft angle for mould release."),
    _spec("catia_shell", "Hollow the part to a wall thickness."),
    _spec("catia_thread", "Add a thread annotation to a cylindrical face."),
    _spec("catia_surface_loft", "Loft a surface through sections."),
    _spec("catia_surface_sweep", "Sweep a profile along a spine."),
    _spec("catia_surface_fill", "Fill a closed boundary with a patch."),
    _spec("catia_curve_helix", "A helix about an axis, by pitch and height."),
    _spec("catia_curve_spline", "An interpolating spline through points."),
    _spec("catia_drawing_create", "Create a drawing from the part."),
    _spec("catia_dimension_add", "Add a dimension to a drawing view."),
    _spec("catia_view_add", "Add a view to a drawing sheet."),
    _spec("catia_assembly_clash", "Check an assembly for interference."),
    _spec("catia_component_add", "Add a component to a product."),
]


class TestNarrowingTheOfferNeverNarrowsWhatCanBeCalled:
    """The property that makes this safe to switch on at all.

    Retrieval that could hide a needed tool is a capability cut wearing an
    optimisation's clothes. The seam is `ToolBox.schemas(only=...)`, which filters
    what is *shown*; `ToolBox.call` is untouched and keeps every tool.
    """

    def test_schemas_narrows_but_call_does_not(self) -> None:
        from app.ai.tools import Tool, ToolBox

        box = ToolBox.__new__(ToolBox)
        object.__setattr__(box, "_tools", {})
        for name in ("alpha", "beta"):
            box._tools[name] = Tool(
                name=name,
                description=name,
                parameters={"type": "object", "properties": {}},
                handler=lambda **_: "ran",
                mutating=False,
            )

        shown = box.schemas(include_mutating=True, only={"alpha"})

        assert [s["function"]["name"] for s in shown] == ["alpha"]
        # The whole point: beta was not offered and still runs.
        assert box.call("beta", {}, allow_mutations=True) == "ran"

    def test_a_name_in_only_that_is_not_a_tool_is_ignored(self) -> None:
        """The selector works from specs and the box from handlers; a bridge that
        went offline mid-turn can legitimately make the two differ."""
        from app.ai.tools import Tool, ToolBox

        box = ToolBox.__new__(ToolBox)
        object.__setattr__(box, "_tools", {})
        box._tools["alpha"] = Tool(
            name="alpha",
            description="a",
            parameters={"type": "object", "properties": {}},
            handler=lambda **_: None,
        )

        assert len(box.schemas(include_mutating=True, only={"alpha", "ghost"})) == 1

    def test_no_only_is_everything(self) -> None:
        from app.ai.tools import Tool, ToolBox

        box = ToolBox.__new__(ToolBox)
        object.__setattr__(box, "_tools", {})
        for name in ("alpha", "beta"):
            box._tools[name] = Tool(
                name=name,
                description=name,
                parameters={"type": "object", "properties": {}},
                handler=lambda **_: None,
            )

        assert len(box.schemas(include_mutating=True)) == 2


class TestTheCoreIsAlwaysShown:
    """The measured failure was a model skipping the modelling loop. Hiding one
    of those because the user said "flange" rather than "sketch" would be the
    same bug approached from the other side."""

    @pytest.mark.parametrize("message", ["make a flange", "loft a surface", "", "draw me a helix"])
    def test_whatever_was_asked(self, message: str) -> None:
        chosen = select_tool_names(REGISTRY, message, limit=15)

        for name in CORE_TOOLS:
            if any(spec.name == name for spec in REGISTRY):
                assert name in chosen, f"{name} was withheld for {message!r}"

    def test_the_core_is_the_modelling_loop_and_the_measurement(self) -> None:
        """Pinned so a future widening is a decision rather than a drift."""
        assert "catia_new_part" in CORE_TOOLS
        assert "catia_sketch_create" in CORE_TOOLS
        assert "catia_pad" in CORE_TOOLS
        assert "catia_measure" in CORE_TOOLS
        assert "catia_surface_loft" not in CORE_TOOLS, "the core is not 'everything useful'"


class TestItFindsWhatWasAskedFor:
    def test_a_fillet_request_offers_the_fillet(self) -> None:
        chosen = select_tool_names(REGISTRY, "round the corners with a fillet", limit=15)

        assert "catia_fillet" in chosen

    def test_a_drawing_request_offers_the_drawing_tools(self) -> None:
        chosen = select_tool_names(REGISTRY, "create a drawing with dimensions", limit=15)

        assert "catia_drawing_create" in chosen
        assert "catia_dimension_add" in chosen

    def test_the_name_is_split_so_a_user_need_not_know_it(self) -> None:
        """`catia_pattern_circular` must be findable from "circular pattern".

        Without splitting on underscores a tool name only matches a user who
        already knew it, which is the opposite of what retrieval is for.
        """
        chosen = select_tool_names(REGISTRY, "repeat it in a circular pattern", limit=15)

        assert "catia_pattern_circular" in chosen

    def test_an_unrelated_request_does_not_pull_in_surfacing(self) -> None:
        """If everything matched everything, the limit would be doing all the work.

        The negative probes are **derived**, not listed. This test named
        `catia_assembly_clash` until 2026-09-08 and then failed on `main` — not
        because retrieval regressed but because the frozen system prompt grew to
        mention that tool, and a prompt-taught tool is an unconditional part of
        the floor. That is deliberate and load-bearing: `select`'s own docstring
        says a limit that could evict a tool the prompt names would teach the
        model to hallucinate the call. So a hand-listed absence here is a claim
        about the prompt's wording, which nothing in this file controls, and it
        rots the day someone writes a good sentence in `prompts.py`.

        What is actually being claimed is narrower and does not rot: a tool that
        no rule reaches — not core, not prompt-taught, not asked for, sharing no
        word with the request — is not offered.
        """
        from app.ai.tool_retrieval import select

        selection = select(REGISTRY, "add a drawing dimension", limit=15)
        chosen = selection.names()

        # Nothing reaches the offer through the *scored* path without scoring.
        # This is the literal reading of "if everything matched everything": the
        # floor rules are allowed to be unconditional, the scorer is not.
        padded = [c.name for c in selection.choices if c.rule == "match" and c.score <= 0.0]
        assert not padded, f"offered by the scorer with no score: {padded}"

        # The surfacing family, which this test is named for. None of them is on
        # the floor and none shares a word with a drafting request.
        for name in ("catia_surface_loft", "catia_surface_sweep", "catia_surface_fill"):
            assert name not in chosen

    def test_a_drafting_request_still_pulls_in_the_draft_angle_tool(self) -> None:
        """A known conflation, recorded rather than hidden.

        `catia_draft` is Part Design's *draft angle for mould release*. It is
        offered for "add a drawing dimension" because the domain rule asks the
        CATIA KB what the message implies, the KB answers with the **Drafting**
        workbench, and the token that entry contributes is `draft` — which is
        also the whole of `catia_draft`'s name. Two unrelated meanings of one
        word, in a rule whose entire job is to bridge vocabulary.

        It costs one extra tool in the offer and hides nothing, which is why it
        is written down here instead of being tuned out in passing: the
        weighting in this module is measured, and changing it belongs with a
        re-run of the retrieval-quality sweep rather than with a red suite.
        """
        from app.ai.tool_retrieval import select

        selection = select(REGISTRY, "add a drawing dimension", limit=15)
        draft = next(c for c in selection.choices if c.name == "catia_draft")

        assert draft.rule == "domain"
        assert "Drafting" in draft.detail

    def test_score_is_normalised_by_the_query_not_the_tool(self) -> None:
        """A long description must not outrank a precise short one by bulk."""
        precise = _spec("catia_fillet", "Round edges.")
        verbose = _spec(
            "catia_shell",
            "Hollow the part to a wall thickness, removing selected faces, useful "
            "for castings and mouldings and enclosures and housings and covers.",
        )
        terms = {"fillet"}

        assert score_tool(precise, terms) > score_tool(verbose, terms)

    def test_no_query_terms_scores_nothing_rather_than_everything(self) -> None:
        assert score_tool(_spec("catia_pad", "Extrude."), set()) == 0.0


class TestContinuity:
    """A model that used a tool last turn is likely to use it again, and a query
    that has moved on to "now measure it" would otherwise drop it."""

    def test_a_recently_used_tool_stays_offered(self) -> None:
        chosen = select_tool_names(
            REGISTRY, "now measure it", recent=["catia_pattern_circular"], limit=15
        )

        assert "catia_pattern_circular" in chosen

    def test_a_recent_name_that_is_not_in_the_registry_is_ignored(self) -> None:
        chosen = select_tool_names(REGISTRY, "measure it", recent=["gone_away"], limit=15)

        assert "gone_away" not in chosen


class TestItIsANoOpWhenItCannotHelp:
    def test_a_limit_of_zero_offers_everything(self) -> None:
        """Zero is the default, and the default must be what happened before."""
        chosen = select_tool_names(REGISTRY, "anything", limit=0)

        assert chosen == {spec.name for spec in REGISTRY}

    def test_a_limit_larger_than_the_registry_offers_everything(self) -> None:
        chosen = select_tool_names(REGISTRY, "anything", limit=len(REGISTRY) + 5)

        assert chosen == {spec.name for spec in REGISTRY}

    def test_it_really_does_reduce_when_it_can(self) -> None:
        """Otherwise every test above passes on a function that does nothing."""
        chosen = select_tool_names(REGISTRY, "round the corners", limit=15)

        assert len(chosen) < len(REGISTRY)

    def test_it_may_exceed_the_limit_to_keep_the_core(self) -> None:
        """The limit bounds the scored set, not the offer. A limit that could
        evict `catia_new_part` would reintroduce the failure this exists for."""
        chosen = select_tool_names(REGISTRY, "loft a surface", limit=3)

        assert len(chosen) > 3
        assert "catia_new_part" in chosen


class TestItCanBeMeasured:
    """16.1's justification is a number, and a change that cannot be measured
    cannot be defended."""

    def test_describe_reports_what_was_withheld(self) -> None:
        chosen = select_tool_names(REGISTRY, "round the corners", limit=15)

        described = describe_selection(REGISTRY, chosen)

        assert described["available"] == len(REGISTRY)
        assert described["offered"] == len(chosen)
        assert described["offered"] < described["available"]
        assert "catia_new_part" in described["core_present"]

    def test_the_default_limit_is_a_starting_point_not_a_tuned_constant(self) -> None:
        assert DEFAULT_LIMIT == 40
