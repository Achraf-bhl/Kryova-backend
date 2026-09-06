"""An unknown command name must not be able to wedge the seat.

Measured twice on 2026-09-06, on a real French V5-R33, during ladder prompts E6
and H1. The model reached for `catia_run_command` with a name CATIA does not
know -- "Close Sketch", then "Fastener Pattern" -- and CATIA answered with a
modal information box, `Entrée clavier: Commande inconnue : <name>`.

That box holds COM. Heartbeats stopped, the device went offline, and **every**
later tool failed, including `catia_describe_dialog` and `catia_dialog_action`
-- the two whose entire purpose is to clear a stuck dialog. The agent tried
`catia_describe_dialog` three times in a row (correctly, having been told a
dialog was probably blocking) and could not reach it. The seat stayed dead
until a human pressed OK.

CLAUDE.md said `StartCommand` "fails silently" and the fallback was built on
that. It does not, and the note there has been corrected. The fix is to
recognise the box and clear it before returning, so an unknown name costs one
refused call instead of the session.

`_is_unknown_command_box` is the part that can be tested off a seat, and it is
the part that carries the risk: dismissing the *wrong* dialog would click OK on
something that matters.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.catia_com import CatiaCom  # noqa: E402


@dataclass
class FakeControl:
    kind: str
    label: str = ""
    value: str = ""
    control_id: int = 0


@dataclass
class FakeDialog:
    title: str
    controls: tuple[FakeControl, ...] = ()

    def fields(self):
        return tuple(
            c for c in self.controls if c.kind in {"text", "choice", "checkbox", "radio"}
        )

    def buttons(self):
        return tuple(c for c in self.controls if c.kind == "button")


is_box = CatiaCom._is_unknown_command_box


class TestRecognisesTheBox:
    def test_the_french_seat_box_from_the_e6_run(self) -> None:
        """Exactly what was on screen, title and text in French."""
        dialog = FakeDialog(
            title="Entrée clavier",
            controls=(
                FakeControl("static", value="Commande inconnue : Close Sketch"),
                FakeControl("button", label="OK", control_id=1),
            ),
        )
        assert is_box(dialog, "Close Sketch") is True

    def test_the_h1_run_box(self) -> None:
        dialog = FakeDialog(
            title="Entrée clavier",
            controls=(
                FakeControl("static", value="Commande inconnue : Fastener Pattern"),
                FakeControl("button", label="OK", control_id=1),
            ),
        )
        assert is_box(dialog, "Fastener Pattern") is True

    def test_it_is_recognised_in_a_language_nobody_wrote_a_table_for(self) -> None:
        """The signal is the echoed command name, not the wording."""
        dialog = FakeDialog(
            title="Tastatureingabe",
            controls=(
                FakeControl("static", value="Unbekannter Befehl: Fastener Pattern"),
                FakeControl("button", label="OK", control_id=1),
            ),
        )
        assert is_box(dialog, "Fastener Pattern") is True

    def test_a_box_with_no_readable_button_still_counts(self) -> None:
        """Some CATIA widgets do not classify; the box must still be cleared."""
        dialog = FakeDialog(
            title="Entrée clavier",
            controls=(FakeControl("other", value="Commande inconnue : Pad"),),
        )
        assert is_box(dialog, "Pad") is True


class TestRefusesToTouchAnythingElse:
    """The dangerous direction. Dismissing a real dialog clicks OK on work."""

    def test_a_real_command_dialog_that_echoes_the_command_name_is_left_alone(self) -> None:
        """The case that isolates the shape check, and the one that matters.

        An English seat's Pad dialog is *titled* "Pad Definition", so the
        echoed-name signal fires on it. Only the shape test -- it has input
        fields and two buttons -- keeps this from being dismissed, and
        dismissing it would press OK on a pad the user is still filling in.

        Written this way deliberately: the first version of this test used a
        French title with no "Pad" in it, so it passed on the name check alone
        and went on passing with the shape check deleted. A guard nobody can
        break is a guard nobody has tested.
        """
        dialog = FakeDialog(
            title="Pad Definition",
            controls=(
                FakeControl("text", label="Length", value="20mm"),
                FakeControl("choice", label="Type", value="Dimension"),
                FakeControl("button", label="OK", control_id=1),
                FakeControl("button", label="Cancel", control_id=2),
            ),
        )
        assert is_box(dialog, "Pad") is False

    def test_a_two_button_box_naming_the_command_is_left_alone(self) -> None:
        """A question is not an information box, however much it echoes."""
        dialog = FakeDialog(
            title="CATIA V5",
            controls=(
                FakeControl("static", value="Abandon Fastener Pattern and continue?"),
                FakeControl("button", label="Yes", control_id=6),
                FakeControl("button", label="No", control_id=7),
            ),
        )
        assert is_box(dialog, "Fastener Pattern") is False

    def test_a_save_prompt_is_left_alone(self) -> None:
        """Two buttons, and it does not echo the command name."""
        dialog = FakeDialog(
            title="CATIA V5",
            controls=(
                FakeControl("static", value="Voulez-vous enregistrer les modifications ?"),
                FakeControl("button", label="Oui", control_id=6),
                FakeControl("button", label="Non", control_id=7),
            ),
        )
        assert is_box(dialog, "Fastener Pattern") is False

    def test_an_unrelated_information_box_is_left_alone(self) -> None:
        """One button and no fields, but nothing to do with the command sent."""
        dialog = FakeDialog(
            title="CATIA V5",
            controls=(
                FakeControl("static", value="La mise à jour a échoué."),
                FakeControl("button", label="OK", control_id=1),
            ),
        )
        assert is_box(dialog, "Fastener Pattern") is False

    def test_no_dialog_is_not_a_box(self) -> None:
        assert is_box(None, "Pad") is False

    def test_an_empty_candidate_never_matches(self) -> None:
        """Otherwise the empty string is a substring of every dialog on screen."""
        dialog = FakeDialog(
            title="CATIA V5",
            controls=(FakeControl("button", label="OK", control_id=1),),
        )
        assert is_box(dialog, "") is False


# ---------------------------------------------------------------------------
# The prevention, which is the only thing that actually works.
#
# Dismissing the box after the fact cannot work and it is worth writing down
# why, because it looks like it should: CATIA raises the modal *synchronously*
# inside `StartCommand`, so that call never returns and no further code on the
# calling thread runs. There is no "afterwards" to clean up in. The only
# defence is never to send a string that can produce the box.
# ---------------------------------------------------------------------------


def test_only_published_ids_ever_reach_startcommand() -> None:
    """The invariant, stated so it keeps holding as the id table grows.

    Anything the server puts in `command_ids` must be a value from
    `COMMAND_IDS` -- a string with a published source. Display labels, however
    plausible, must not appear there, because a label CATIA does not recognise
    as an id is what raises the modal that kills the seat.

    `COMMAND_IDS` was unreachable when this was written -- both keys named
    `infrastructure.*` entries that were never authored, so the id fallback was
    dead code that read as live. Fixed on 2026-09-06 by re-keying the one id
    whose command is actually in the reference (`ui.new_window` ->
    `OpenInNewWnd`) and dropping the other rather than inventing an entry for
    it. `tests/test_command_ids.py` now holds both halves of that as a standing
    check, so it cannot rot back silently. This test is unaffected either way:
    it asserts the *ceiling*, and a table that is empty and one that is
    reachable both satisfy it.
    """
    from app.catia.dispatch import _enrich
    from app.catia.tool_specs import get_spec
    from app.catia_kb.ui import COMMAND_IDS

    spec = get_spec("catia_run_command")
    assert spec is not None
    published = set(COMMAND_IDS.values())

    for command, language in [
        ("Fastener Pattern", "fr"),
        ("Close Sketch", "fr"),
        ("Edge Fillet", "en"),
        ("Open in New Window", "en"),
        ("Pad", "de"),
    ]:
        built = _enrich(
            None,  # type: ignore[arg-type]
            spec=spec,
            document=None,
            arguments={"command": command},
            language=language,
        )
        assert set(built["command_ids"]) <= published, built
        # Labels keep flowing: they are how the live menu is matched, which is
        # the safe path and the one that actually works.
        assert built["candidates"], built


def test_a_label_with_no_published_id_is_refused_rather_than_gambled() -> None:
    """The exact call that killed two sessions, against the mock backend."""
    import tempfile

    from catia_bridge.backend import CatiaOperationError as _Err
    from catia_bridge.mock_catia import MockCatia

    with tempfile.TemporaryDirectory() as tmp:
        catia = MockCatia(Path(tmp) / "catia")
        catia.new_part(name="plate")
        try:
            result = catia.run_command(
                command="Fastener Pattern",
                candidates=["Fastener Pattern"],
                command_ids=[],
                command_name="Fastener Pattern",
            )
        except _Err as exc:
            # The mock may refuse; what matters is that it never reports the
            # command as having run.
            assert "Fastener Pattern" in str(exc)
        else:
            assert not result.get("verified", False), result
