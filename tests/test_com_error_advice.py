"""A raw CATIA COM failure must reach the agent with something to do about it.

83 of the bridge's feature-creating calls have no handler of their own -- the
`com/` mixins cover Part Design, surfaces, wireframe, reference geometry and
assembly, and writing bespoke advice at each site is neither feasible nor
durable. What every one of them produces on failure has the same shape:

    (-2147352567, "Une exception s'est produite.",
     (0, 'CATIAShapeFactory',
      'La methode AddNewSolidEdgeFilletWithConstantRadius a echoue',
      None, 0, -2147467259), None)

Measured on ladder prompt H3, 2026-09-06. The agent, given that, retried the
identical call, then abandoned the modelling tools and began driving CATIA's
menus by hand; the round budget ran out with a block and no fillets on it.

`com_errors.explain` is one place that covers all of them, and it works because
**the CATIA method name inside is not localised**. The sentence around it is --
`La methode X a echoue` here, `The method X failed` on an English seat -- but X
is the automation API name and is identical on every install. That is the same
property `api.localisation` rests on for the whole bridge.

The second half of this file is `catia_select`'s refusal, from the same run: the
agent burned four rounds on `all`, `vertical` and `front_left`, which are the
selector words `catia_fillet` and `catia_hole` take. The message told it they
were not features -- true, and no help, because they are the right words on the
wrong tool.

Offline: no CATIA, no seat.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.com_errors import advice_for, explain, failed_method  # noqa: E402
from catia_bridge.tool_table import (  # noqa: E402
    EDGE_SELECTORS,
    FACE_POSITIONS,
    not_in_this_part,
)


def com_error(method: str, *, language: str = "fr") -> Exception:
    """A COM error shaped exactly like the seat's, in a chosen language."""
    wording = {
        "fr": f"La methode {method} a echoue",
        "en": f"The method {method} failed",
        "de": f"Die Methode {method} ist fehlgeschlagen",
    }[language]
    return Exception(
        f"(-2147352567, 'Une exception...', (0, 'CATIAShapeFactory', "
        f"'{wording}', None, 0, -2147467259), None)"
    )


class TestFindingTheMethod:
    @pytest.mark.parametrize("language", ["fr", "en", "de"])
    def test_the_api_name_is_found_in_any_language(self, language: str) -> None:
        """The whole design. The wrapper text is translated; the name is not."""
        found = failed_method(str(com_error("AddNewPocket", language=language)))
        assert found == "AddNewPocket"

    def test_the_exact_h3_error(self) -> None:
        assert failed_method(
            str(com_error("AddNewSolidEdgeFilletWithConstantRadius"))
        ) == "AddNewSolidEdgeFilletWithConstantRadius"

    def test_an_error_naming_no_method_returns_nothing(self) -> None:
        assert failed_method("The connection was lost") is None


class TestTheAdvice:
    @pytest.mark.parametrize(
        ("method", "must_mention"),
        [
            ("AddNewPad", "closed loop"),
            ("AddNewPocket", "closed loop"),
            ("AddNewShaft", "closed loop"),
            ("AddNewSolidEdgeFilletWithConstantRadius", "too large"),
            ("AddNewChamfer", "too large"),
            ("AddNewDraft", "too large"),
            ("AddNewHoleFromPoint", "bounding box"),
            ("AddNewCircPattern", "bounding box"),
            ("AddNewLoft", "closed loop"),
            ("AddNewSweepExplicit", "guide"),
            ("AddNewJoin", "joined"),
            ("AddNewPlaneOffset", "construction geometry"),
        ],
    )
    def test_each_family_gets_advice_about_its_own_failure(
        self, method: str, must_mention: str
    ) -> None:
        advice = advice_for(str(com_error(method)))
        assert advice is not None, method
        assert must_mention in advice, (method, advice)

    def test_the_longest_matching_prefix_wins(self) -> None:
        """Where one key is a prefix of another, the longer one answers.

        Stated over the real table rather than through an example, because
        checking it with a hand-picked method was vacuous: no two keys are
        currently prefix-related, so any pair I chose passed with the
        longest-first sort removed. Written this way it is a real check the day
        someone adds `AddNewShellFace` beside `AddNewShell` with different
        advice -- which is when a shorter key silently answering for a longer
        method starts handing back advice about a different operation. That is
        worse than none, because the agent acts on it.
        """
        from catia_bridge.com_errors import _ADVICE

        pairs = [
            (short, long)
            for short in _ADVICE
            for long in _ADVICE
            if long != short and long.startswith(short)
        ]
        for short, long in pairs:
            assert advice_for(str(com_error(long))) == _ADVICE[long], (
                f"{long!r} is being answered by {short!r}"
            )
        # And the sort that makes it hold is present, so removing it fails here
        # even while the table has no such pair to catch it.
        import inspect

        from catia_bridge import com_errors

        assert "key=len, reverse=True" in inspect.getsource(com_errors.advice_for)

    def test_an_unrecognised_method_gets_no_invented_cause(self) -> None:
        """None, not a guess. A wrong cause is acted on; a missing one is not."""
        assert advice_for(str(com_error("AddNewSomethingNobodyHasWritten"))) is None


class TestTheMessage:
    def test_catias_own_words_always_survive(self) -> None:
        """They are the only thing distinguishing one instance from another."""
        message = explain("catia_pocket", com_error("AddNewPocket"))
        assert "AddNewPocket a echoue" in message

    def test_it_names_the_tool_and_the_method(self) -> None:
        message = explain("catia_fillet_edges", com_error("AddNewSolidFaceFillet"))
        assert "catia_fillet_edges" in message
        assert "AddNewSolidFaceFillet" in message

    def test_the_exception_type_is_kept(self) -> None:
        """The one piece of the old message worth keeping.

        A COM tuple says nothing alone, and `com_error` versus `TimeoutError`
        versus `AttributeError` is often all that separates "CATIA refused
        this" from "the bridge called a method this release does not have".
        `test_catia_daemon.py` asserts it too, from the daemon's side.
        """
        assert "RuntimeError" in explain("catia_measure", RuntimeError("COM error 0x80004005"))

    def test_an_unrecognised_failure_still_beats_the_bare_error(self) -> None:
        """The old message was `RuntimeError while running catia_x: <com tuple>`.
        Even with no family advice this has to say nothing was changed."""
        message = explain("catia_x", Exception("something else entirely"))
        assert "catia_x" in message
        assert "Nothing was changed" in message
        assert "something else entirely" in message


class TestSelectRefusal:
    def test_a_selector_word_is_told_where_it_belongs(self) -> None:
        """The four wasted rounds on H3."""
        for word in ("all", "vertical", "front_left"):
            message = not_in_this_part([word])
            assert "not feature names" in message, word
            assert "catia_fillet" in message, word

    def test_the_vocabularies_come_from_the_schemas(self) -> None:
        """Restating them here would let the message drift from the tools."""
        assert "vertical" in EDGE_SELECTORS
        assert "front_left" in FACE_POSITIONS

    def test_a_genuine_typo_gets_no_irrelevant_lecture(self) -> None:
        """`Pad.9` is a missing feature, not a misplaced selector. Appending the
        selector paragraph to every refusal would bury the real answer."""
        message = not_in_this_part(["Pad.9"])
        assert "Pad.9" in message
        assert "catia_fillet" not in message

    def test_the_original_advice_is_still_there(self) -> None:
        for word in ("Pad.9", "vertical"):
            assert "catia_list_features" in not_in_this_part([word])

    def test_an_edge_id_is_told_which_tool_takes_it(self) -> None:
        """The next round of the same H3 run, after the selector-word fix.

        Told that selector words were not feature names, the agent did the
        right thing: it called catia_list_edges, got 'Edge.1' ... 'Edge.16',
        and handed eight of them to catia_select -- which refused them and
        pointed at catia_list_features, the one tool that cannot help. An id
        this system prints and then refuses, without naming the tool that
        takes it, is a dead end the system built for itself.
        """
        message = not_in_this_part(["Edge.1", "Edge.2", "Edge.5"])
        assert "catia_fillet_edges" in message
        assert "catia_list_edges" in message

    def test_a_long_list_of_ids_does_not_repeat_them_all(self) -> None:
        """Eight ids restated twice is a paragraph the model has to read past
        to reach the answer."""
        message = not_in_this_part([f"Edge.{n}" for n in range(1, 12)])
        assert "..." in message

    def test_a_face_id_gets_the_same_treatment(self) -> None:
        assert "catia_fillet_edges" in not_in_this_part(["Face.2"])

    def test_a_feature_name_is_not_lectured_about_edges(self) -> None:
        """`Pad.9` is a missing feature. Appending the topology paragraph to
        every refusal would bury the real answer under advice about a tool the
        caller was not using."""
        message = not_in_this_part(["Pad.9"])
        assert "catia_fillet_edges" not in message

    def test_a_mixed_list_gets_both_paragraphs(self) -> None:
        """The agent that sends 'vertical' and 'Edge.1' in one call has two
        different misunderstandings and needs both answers."""
        message = not_in_this_part(["vertical", "Edge.1"])
        assert "catia_fillet_edges" in message
        assert "not feature names" in message
