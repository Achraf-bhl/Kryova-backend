"""Tool selection against the registry the product actually ships — 16.1.

`tests/test_tool_retrieval.py` proves the machinery on a synthetic thirty-tool
registry: fast, deterministic, and blind to the thing that matters. This file
measures the *real* one — 110 OCCT operations plus the built-in tools — with the
messages from the runs recorded in `docs/verification-2026-09-0{5,6}/REPORT.md`.
The split is the same one `test_retrieval.py` and `test_retrieval_corpus.py`
have for the manuals, and for the same reason: every synthetic test passed while
the shipped selector was withholding `catia_set_parameter` on every message.

**The tools a message "actually needs" are not invented here.** They are the
tool names that appear in the transcripts of the runs those reports describe —
the ten calls of the passing plate run, the correction sequence of attempt 4,
the clearance question rung 5 asks. A list of tools somebody thought a message
might want would measure nothing.

Offline and fast: the registry is already in memory and there is no index.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai import tool_retrieval
from app.ai.tool_retrieval import (
    CORE_TOOLS,
    INTENT_FAMILIES,
    discriminating_terms,
    prompt_taught_tools,
    select,
)
from app.ai.tools import BUILTIN_TOOL_LABELS

LIMIT = 40


def _spec(name: str, description: str = "") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=description)


@pytest.fixture(scope="module")
def registry() -> list[SimpleNamespace]:
    """The vocabulary the OCCT deployment offers, read from the code.

    Intersected with what the kernel implements, exactly as
    `dispatch.offered_tool_specs` does, so this is the list the agent is really
    choosing from and not a superset nobody ships.
    """
    from app.catia import tool_specs
    from app.geometry import backends

    implemented = backends.local_tool_names()
    if not implemented:  # pragma: no cover - a machine with no OCCT wheel
        pytest.skip("the OCCT kernel is not importable here")
    specs = [
        _spec(spec.name, spec.description)
        for spec in tool_specs.CATIA_TOOL_SPECS
        if spec.name in implemented
    ]
    # Plus the built-ins the toolbox adds; `check_part` is one of them and it is
    # in CORE_TOOLS, so leaving them out would make the core untestable here.
    specs += [_spec(name, label) for name, label in BUILTIN_TOOL_LABELS.items()]
    return specs


@pytest.fixture(scope="module")
def names(registry: list[SimpleNamespace]) -> set[str]:
    return {spec.name for spec in registry}


#: The five prompts driven through the real chat endpoint in the recorded
#: sessions, plus the two rungs above the one that has been reached.
PLATE = (
    "Make me a steel plate 200 mm by 150 mm and 10 mm thick, with a 60 mm "
    "diameter bore through the centre. Then measure it and tell me its mass."
)
RUNG3 = (
    "Design a steel mounting plate: 200 mm by 150 mm, with a 60 mm diameter "
    "bore through the centre and four 12 mm clearance holes, one 20 mm in from "
    "each corner. It has to weigh 2.4 kg. Pick a starting thickness, build it, "
    "measure the mass, and then adjust the thickness until the measured mass is "
    "within 20 grams of 2.4 kg."
)
BOLT_CIRCLE = "put four M8 clearance holes on a 70 mm bolt circle"
MASS_TARGET = "make it weigh 2.4 kg"
CLEARANCE = "check it clears through the travel"

MESSAGES = [PLATE, RUNG3, BOLT_CIRCLE, MASS_TARGET, CLEARANCE]


class TestTheRegistryIsWorthNarrowing:
    def test_it_is_the_size_the_board_says_it_is(self, registry: list[SimpleNamespace]) -> None:
        """If this ever drops below the limit the rest of the file is vacuous."""
        assert len(registry) > LIMIT * 2

    @pytest.mark.parametrize("message", MESSAGES)
    def test_the_offer_is_a_fraction_of_it(
        self, registry: list[SimpleNamespace], message: str
    ) -> None:
        chosen = select(registry, message, limit=LIMIT)

        assert len(chosen.choices) < len(registry) * 0.6

    @pytest.mark.parametrize("message", MESSAGES)
    def test_the_limit_is_a_ceiling_not_a_quota(
        self, registry: list[SimpleNamespace], message: str
    ) -> None:
        """Leftover slots stay empty.

        Filling them with the alphabetical remainder would contradict the
        finding this exists for — that a bigger payload is worse. The floor and
        the intent tables may push a little past the limit; the speculative
        family rule may not.
        """
        chosen = select(registry, message, limit=LIMIT)

        assert len(chosen.choices) <= LIMIT + len(CORE_TOOLS)


class TestTheToolsTheRecordedRunsActuallyUsed:
    """Derived from the transcripts, not from an opinion about what fits.

    Each list is the set of tool names the corresponding recorded run called (or,
    for the two rungs not yet reached, the ones the failure report names as the
    tools that were needed and not reached for).
    """

    @pytest.mark.parametrize(
        ("message", "needed"),
        [
            pytest.param(
                PLATE,
                # The ten calls of the passing run, 2026-09-06, 234 s.
                [
                    "catia_new_part",
                    "catia_sketch_create",
                    "catia_sketch_rectangle",
                    "catia_pad",
                    "catia_sketch_circle",
                    "catia_pocket",
                    "catia_set_material",
                    "catia_measure",
                    "check_part",
                ],
                id="plate-that-passed",
            ),
            pytest.param(
                RUNG3,
                # The above plus the correction pair. `catia_set_parameter` is
                # the tool attempt 4 finally used and the three attempts before
                # it never found; `catia_list_parameters` is how it learns the
                # name and the unit it must pass.
                [
                    "catia_new_part",
                    "catia_sketch_create",
                    "catia_pad",
                    "catia_pocket",
                    "catia_set_material",
                    "catia_measure",
                    "catia_set_parameter",
                    "catia_list_parameters",
                    "check_part",
                ],
                id="rung-3-measure-and-correct",
            ),
            pytest.param(
                BOLT_CIRCLE,
                ["catia_sketch_circle", "catia_pocket", "catia_hole", "catia_pattern_circular"],
                id="rung-2-bolt-circle",
            ),
            pytest.param(
                MASS_TARGET,
                [
                    "catia_set_material",
                    "catia_measure",
                    "catia_list_parameters",
                    "catia_set_parameter",
                    "check_part",
                ],
                id="mass-target-alone",
            ),
            pytest.param(
                CLEARANCE,
                ["catia_measure", "catia_measure_between", "catia_list_faces"],
                id="rung-5-clearance",
            ),
        ],
    )
    def test_every_one_of_them_is_offered(
        self,
        registry: list[SimpleNamespace],
        names: set[str],
        message: str,
        needed: list[str],
    ) -> None:
        chosen = select(registry, message, limit=LIMIT)

        missing = [name for name in needed if name in names and name not in chosen.names()]
        assert not missing, (
            f"{missing} were needed by the recorded run and would not have been "
            f"offered. Offer was {sorted(chosen.names())}"
        )

    def test_set_parameter_was_the_measured_failure_and_is_now_always_there(
        self, registry: list[SimpleNamespace], names: set[str]
    ) -> None:
        """The specific regression, pinned on its own.

        Measured 2026-09-06 against the shipped selector: `catia_set_parameter`
        was withheld on all five of these messages, including the one that is
        entirely about adjusting a parameter. Three sessions running, the agent
        rebuilt the pad instead of changing it.
        """
        assert "catia_set_parameter" in names
        for message in MESSAGES:
            assert "catia_set_parameter" in select(registry, message, limit=LIMIT).names()


class TestNothingTheSystemPromptTeachesIsEverWithheld:
    """The binding this module could otherwise break.

    There are four frozen system prompts rather than one built by concatenation
    precisely so a deployment never describes a tool the model was not given —
    that is what teaches it to hallucinate a call. Narrowing the offer put that
    back: measured 2026-09-06, five to nine prompt-named tools were withheld on
    every realistic message.
    """

    def test_the_prompts_do_name_tools(self) -> None:
        taught = prompt_taught_tools()

        assert len(taught) > 20, "if the prompts named nothing, the rule below is vacuous"
        assert "catia_set_parameter" in taught
        assert "catia_new_part" in taught

    def test_it_is_read_from_the_prompt_text_not_tabulated(self) -> None:
        """So it cannot fall behind an edit to `prompts.py`."""
        from app.ai import prompts

        text = "\n".join(
            value
            for name, value in vars(prompts).items()
            if not name.startswith("_") and isinstance(value, str)
        )
        for name in prompt_taught_tools():
            assert name in text

    def test_the_prompts_never_name_a_tool_that_does_not_exist(self) -> None:
        """The other half of the same rule, and cheap to check here.

        Withholding a tool the prompt names teaches the model to hallucinate the
        call. So does *naming one that was never built* — the offer cannot save
        it, because there is nothing to offer.

        Known and deliberate gap, recorded rather than asserted: ten of these
        exist on a CATIA seat and not on the open kernel (`catia_run_command`
        and the interactive dialog family, `catia_export_step`,
        `catia_capture_view`, `catia_open_document`). Closing that would mean a
        fifth prompt axis — eight frozen prompts rather than four — which is a
        design decision belonging to whoever takes it, not a side effect of tool
        selection.
        """
        from app.catia import tool_specs

        real = {spec.name for spec in tool_specs.CATIA_TOOL_SPECS} | set(BUILTIN_TOOL_LABELS)

        phantom = sorted(prompt_taught_tools() - real)
        assert not phantom, f"the prompts name {phantom}, which no registry has"

    @pytest.mark.parametrize("message", [*MESSAGES, "", "thanks, that looks great", "go on"])
    def test_none_of_them_is_ever_withheld(
        self, registry: list[SimpleNamespace], names: set[str], message: str
    ) -> None:
        chosen = select(registry, message, limit=LIMIT).names()

        withheld = sorted((prompt_taught_tools() & names) - chosen)
        assert not withheld, f"the prompt names {withheld} and the offer does not carry them"

    def test_breaking_the_floor_reproduces_the_shipped_defect(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard, verified by breaking the thing it guards.

        `catia_groove` is offered on every message measured, and *only* the
        prompt floor offers it — no intent table names it and no realistic
        message shares a word with it. Remove the floor and it goes, which is
        what the selector did to five to nine prompt-named tools per message
        before this rule existed.

        `catia_set_parameter` is deliberately not the example any more: since
        the `mass target` and `correction` intent tables were added, two
        independent rules supply it, so removing one proves nothing about it.
        That redundancy is wanted — it is just not a demonstration.
        """
        assert "catia_groove" in select(registry, MASS_TARGET, limit=LIMIT).names()

        monkeypatch.setattr(tool_retrieval, "prompt_taught_tools", frozenset)

        assert "catia_groove" not in select(registry, MASS_TARGET, limit=LIMIT).names()


class TestTheSelectionSaysWhy:
    """A silent failure needs a written reason.

    The failure this module can cause is a tool that is needed, absent, and
    never mentioned: the model does something else and the part comes out wrong
    with every call returning `ok`. The only thing that makes that diagnosable
    afterwards is a record of what was offered and which rule offered it.
    """

    def test_every_offered_tool_carries_one(self, registry: list[SimpleNamespace]) -> None:
        chosen = select(registry, RUNG3, limit=LIMIT)

        for name in chosen.names():
            reason = chosen.why(name)
            assert reason and ":" in reason

    def test_a_tool_that_was_not_offered_has_no_reason(
        self, registry: list[SimpleNamespace]
    ) -> None:
        chosen = select(registry, MASS_TARGET, limit=LIMIT)

        assert chosen.why("catia_surface_loft") is None

    def test_the_reason_names_the_evidence(self, registry: list[SimpleNamespace]) -> None:
        chosen = select(registry, "sweep a profile along a helix to make a spring", limit=LIMIT)

        assert "helix" in (chosen.why("catia_curve_helix") or "")

    def test_the_rules_are_a_closed_set(self, registry: list[SimpleNamespace]) -> None:
        """A new rule must be named, so a log of them can be counted."""
        allowed = {"core", "prompt", "recent", "match", "domain", "intent", "family", "all"}

        for message in MESSAGES:
            assert set(select(registry, message, limit=LIMIT).by_rule()) <= allowed

    def test_the_log_line_carries_the_numbers_the_board_wants(
        self, registry: list[SimpleNamespace]
    ) -> None:
        described = select(registry, RUNG3, limit=LIMIT).to_dict()

        assert described["available"] == len(registry)
        assert described["offered"] == len(select(registry, RUNG3, limit=LIMIT).choices)
        assert described["narrowed"] is True
        assert described["limit"] == LIMIT

    def test_a_no_op_selection_says_it_was_one(self, registry: list[SimpleNamespace]) -> None:
        described = select(registry, RUNG3, limit=0).to_dict()

        assert described["narrowed"] is False
        assert described["offered"] == len(registry)


class TestTheCatiaReferenceIsUsedRatherThanReimplemented:
    """`app/catia_kb/recognise.py` already knows that *bore* means Hole.

    Consuming it is what makes a word the tool names do not contain reach the
    right tool — and, for free, what makes a French request work, since the
    reference carries the interface names in five languages.
    """

    def test_a_word_no_tool_name_contains_still_reaches_the_tool(
        self, registry: list[SimpleNamespace], names: set[str]
    ) -> None:
        assert "bore" not in " ".join(names), "this test needs 'bore' to be absent from the names"

        chosen = select(registry, "a 60 mm diameter bore through the centre", limit=LIMIT)

        assert "catia_hole_at" in chosen.names()
        assert "Hole" in (chosen.why("catia_hole_at") or "")

    def test_the_same_request_in_french(self, registry: list[SimpleNamespace]) -> None:
        """`trou` is French for hole and appears nowhere in this codebase."""
        chosen = select(registry, "perce un trou de 12 mm au centre", limit=LIMIT)

        assert "catia_hole_at" in chosen.names()

    def test_breaking_it_loses_the_tool(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard, verified by breaking the thing it guards.

        The intent tables are switched off first and stay off in both halves,
        because `bore` is also one of their triggers and would otherwise supply
        the tool either way — which would make this test pass on a broken
        reference lookup. What is being isolated is the reference lookup alone.
        """
        monkeypatch.setattr(tool_retrieval, "_family_triggers", tuple)
        message = "a 60 mm diameter bore through the centre"
        assert "catia_hole_at" in select(registry, message, limit=LIMIT).names()

        monkeypatch.setattr(tool_retrieval, "_domain_terms", lambda text: {})

        assert "catia_hole_at" not in select(registry, message, limit=LIMIT).names()

    def test_an_inferred_term_never_outranks_the_users_own(self) -> None:
        """`DOMAIN_WEIGHT` — expansion widens, it does not reorder."""
        spoken = _spec("catia_fillet", "Round edges.")
        inferred = _spec("catia_hole", "Cut a hole.")

        assert tool_retrieval.score_tool(spoken, {"fillet"}, {"hole"}) > tool_retrieval.score_tool(
            inferred, {"fillet"}, {"hole"}
        )


class TestWhatTheTaskNeedsRatherThanWhatItNames:
    """`INTENT_FAMILIES` — the rule lexical matching cannot express.

    "adjust the thickness until the measured mass is within 20 grams" shares no
    word with `catia_set_parameter`. No amount of scoring fixes that, because
    the tool the task needs and the way the task is asked for have disjoint
    vocabularies. A small explicit table can, and is honest about being a table.
    """

    def test_every_tool_named_in_the_tables_exists(self) -> None:
        """A rename elsewhere must fail here rather than silently offer nothing."""
        from app.catia import tool_specs

        real = {spec.name for spec in tool_specs.CATIA_TOOL_SPECS} | set(BUILTIN_TOOL_LABELS)

        unknown = sorted(
            {name for _, tools in INTENT_FAMILIES.values() for name in tools} - real
        )
        assert not unknown, f"{unknown} are named by an intent family and do not exist"

    def test_a_clearance_question_offers_something_that_can_measure_one(
        self, registry: list[SimpleNamespace]
    ) -> None:
        """Measured with the shipped selector, this message matched nothing at
        all and collapsed the offer to nine tools, not one of which could
        measure a distance between two things."""
        chosen = select(registry, CLEARANCE, limit=LIMIT)

        assert "catia_measure_between" in chosen.names()
        assert chosen.why("catia_measure_between", ) is not None
        assert "clearance" in (chosen.why("catia_measure_between") or "")

    def test_a_mass_target_offers_the_material_and_the_parameter(
        self, registry: list[SimpleNamespace]
    ) -> None:
        chosen = select(registry, MASS_TARGET, limit=LIMIT)

        assert {"catia_set_material", "catia_list_parameters"} <= chosen.names()

    def test_breaking_the_tables_loses_the_clearance_tools(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tool_retrieval, "_family_triggers", tuple)

        chosen = select(registry, CLEARANCE, limit=LIMIT)

        assert "catia_measure_between" not in chosen.names()

    def test_they_are_unconditional_rather_than_bounded_by_the_limit(
        self, registry: list[SimpleNamespace]
    ) -> None:
        """A cap here would put the failure back for exactly the long, detailed
        requests that hit the cap — which are the ones that need it most."""
        chosen = select(registry, RUNG3, limit=1)

        assert "catia_list_parameters" in chosen.names()


class TestTheNoiseFilters:
    """Two rules that stop the registry talking to itself."""

    def test_a_word_every_tool_uses_is_dropped(
        self, registry: list[SimpleNamespace]
    ) -> None:
        useful = discriminating_terms(registry)

        # `sketch` and `part` are in dozens of descriptions, so they say nothing
        # about which tool. `helix` and `fillet` are in one or two, so they do.
        assert "sketch" not in useful
        assert "part" not in useful
        assert "helix" in useful
        assert "fillet" in useful

    def test_a_bare_numeral_never_names_a_tool(
        self, registry: list[SimpleNamespace]
    ) -> None:
        """Measured: "make it weigh 2.4 kg" analysed to `2` and `4`, which
        matched `catia_boolean` and `catia_point_on_surface` through phrases
        like "2 points"."""
        chosen = select(registry, MASS_TARGET, limit=LIMIT)

        assert "catia_boolean" not in chosen.names()
        assert "catia_point_on_surface" not in chosen.names()

    def test_growing_the_registry_does_not_quietly_readmit_a_noise_word(
        self, registry: list[SimpleNamespace]
    ) -> None:
        """The share is not scale-free, and that is how this broke once.

        `COMMON_TERM_SHARE` was measured on a 110-tool registry. E16.2's three
        planning built-ins plus `catia_export_step` took it to 142, the ceiling
        moved 27 -> 28, and `sketch` — carried by exactly 28 tools — was
        readmitted as "discriminating" by four tools that have nothing to do
        with sketching. Nothing about sketching had changed.

        So this asserts the *margin*, not just the verdict: a word carried by a
        fifth of the registry must sit clear of the boundary, or the next four
        tools anybody adds flip it back with no test to say so.
        """
        counts: dict[str, int] = {}
        for spec in registry:
            for term in tool_retrieval._spec_terms(str(spec.name), str(spec.description)):
                counts[term] = counts.get(term, 0) + 1
        ceiling = max(1, int(len(registry) * tool_retrieval.COMMON_TERM_SHARE))

        # Four more tools is one ordinary phase's worth of registry growth.
        headroom = max(1, int((len(registry) + 4) * tool_retrieval.COMMON_TERM_SHARE))
        for noise in ("sketch", "part"):
            assert counts[noise] > ceiling, f"{noise!r} is no longer filtered"
            assert counts[noise] > headroom, (
                f"{noise!r} is filtered only by {counts[noise] - headroom} tools' margin; "
                "four more tools in the registry would readmit it"
            )

    def test_a_term_in_the_name_is_a_match_and_one_in_the_prose_is_not(self) -> None:
        """`DESCRIPTION_WEIGHT`, shown by moving one word.

        The two specs differ only in where the word `widget` sits. In the name
        it is a match; in the description alone it is one corroborating term,
        which is not enough on its own — that is what stopped `catia_boolean`
        being offered for "make it weigh 2.4 kg" on the word *make*.
        """
        haystack = [_spec(f"catia_filler_{n}", "nothing relevant here") for n in range(30)]

        in_prose = select(
            [*haystack, _spec("catia_thing", "a widget for widgeting")], "widget", limit=5
        )
        in_name = select(
            [*haystack, _spec("catia_widget", "a thing for thinging")], "widget", limit=5
        )

        assert "catia_thing" not in in_prose.names()
        assert "catia_widget" in in_name.names()

    def test_two_prose_terms_are_enough(self) -> None:
        """The rule is "one is not enough", not "the description is ignored"."""
        haystack = [_spec(f"catia_filler_{n}", "nothing relevant here") for n in range(30)]

        chosen = select(
            [*haystack, _spec("catia_thing", "a widget for gribbling")],
            "widget gribbling",
            limit=5,
        )

        assert "catia_thing" in chosen.names()

    def test_breaking_the_common_word_filter_lets_the_noise_back(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With every term counted as discriminating, "create a sketch" starts
        matching sketch tools on the word `sketch`, which cannot separate one of
        the thirty from another."""
        baseline = select(registry, "create a sketch", limit=LIMIT).names()
        monkeypatch.setattr(
            tool_retrieval,
            "discriminating_terms",
            lambda specs: frozenset(
                term
                for spec in specs
                for term in tool_retrieval._spec_terms(
                    str(spec.name), str(getattr(spec, "description", ""))
                )
            ),
        )

        widened = select(registry, "create a sketch", limit=LIMIT).names()

        assert len(widened) > len(baseline)


class TestTheConversationSoFarCounts:
    """A thin turn must not lose the vocabulary a thick one established."""

    def test_a_bare_go_on_keeps_the_previous_turns_tools(
        self, registry: list[SimpleNamespace]
    ) -> None:
        alone = select(registry, "go on", limit=LIMIT).names()
        carried = select(registry, "go on", context=BOLT_CIRCLE, limit=LIMIT).names()

        assert "catia_hole_at" not in alone
        assert "catia_hole_at" in carried

    def test_a_tool_used_last_turn_stays_offered(
        self, registry: list[SimpleNamespace]
    ) -> None:
        chosen = select(
            registry, "now measure it", recent=["catia_pattern_circular"], limit=LIMIT
        )

        assert chosen.why("catia_pattern_circular") is not None


class TestTheWiring:
    """`agent._shown_tools` is the one place any of this reaches the product.

    A stub toolbox rather than a real one: what is under test is the wiring —
    the setting, the fallback and the arguments passed — and building a real
    `ToolBox` would drag a database into a file that runs offline in a second.
    """

    def _toolbox(self, registry: list[SimpleNamespace], **kwargs: object) -> object:
        return SimpleNamespace(
            every_tool=lambda: registry,
            recent_tool_names=lambda: kwargs.get("recent", []),
            recent_user_messages=lambda: kwargs.get("context", ""),
        )

    def test_it_is_off_by_default(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`AI_TOOL_LIMIT` defaults to 0, and 0 must mean exactly what every
        deployment did before retrieval existed."""
        from app.ai import agent
        from app.core.config import settings

        monkeypatch.setattr(settings, "ai_tool_limit", 0, raising=False)

        assert agent._shown_tools(self._toolbox(registry), RUNG3) is None

    def test_switched_on_it_narrows(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ai import agent
        from app.core.config import settings

        monkeypatch.setattr(settings, "ai_tool_limit", LIMIT, raising=False)

        shown = agent._shown_tools(self._toolbox(registry), RUNG3)

        assert shown is not None
        assert "catia_set_parameter" in shown
        assert len(shown) < len(registry)

    def test_the_conversation_so_far_reaches_the_selector(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ai import agent
        from app.core.config import settings

        monkeypatch.setattr(settings, "ai_tool_limit", LIMIT, raising=False)

        alone = agent._shown_tools(self._toolbox(registry), "go on")
        carried = agent._shown_tools(
            self._toolbox(registry, context=BOLT_CIRCLE), "go on"
        )

        assert alone is not None and carried is not None
        assert "catia_hole_at" not in alone
        assert "catia_hole_at" in carried

    def test_a_failure_falls_back_to_the_whole_registry(
        self, registry: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`KnowledgeService.search`'s contract, applied here: consulting an
        index may improve an answer and must never be why there is not one.
        Verified by breaking it — a toolbox that raises must still leave the
        agent with every tool rather than with none."""
        from app.ai import agent
        from app.core.config import settings

        monkeypatch.setattr(settings, "ai_tool_limit", LIMIT, raising=False)

        def explode() -> list[SimpleNamespace]:
            raise RuntimeError("the registry went away")

        broken = SimpleNamespace(
            every_tool=explode,
            recent_tool_names=list,
            recent_user_messages=str,
        )

        assert agent._shown_tools(broken, RUNG3) is None


class TestItStillNeverNarrowsWhatCanBeCalled:
    """The property that makes any of this safe, checked once against the real
    toolbox seam rather than only against the synthetic registry."""

    def test_the_offer_is_a_filter_on_schemas_and_nothing_else(
        self, registry: list[SimpleNamespace]
    ) -> None:
        from app.ai.tools import Tool, ToolBox

        box = ToolBox.__new__(ToolBox)
        object.__setattr__(box, "_tools", {})
        for spec in registry[:20]:
            box._tools[spec.name] = Tool(
                name=spec.name,
                description=spec.description,
                parameters={"type": "object", "properties": {}},
                handler=lambda **_: "ran",
            )
        offer = select(registry, CLEARANCE, limit=LIMIT).names()

        shown = {s["function"]["name"] for s in box.schemas(include_mutating=True, only=offer)}

        assert shown < set(box._tools), "the offer did not narrow anything"
        hidden = next(name for name in box._tools if name not in shown)
        assert box.call(hidden, {}, allow_mutations=True) == "ran"
