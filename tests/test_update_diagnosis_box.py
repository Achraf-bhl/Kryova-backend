"""CATIA's update-diagnosis modal holds COM, and the bridge can clear it.

Measured on the seat, 2026-09-06, ladder prompt H4 run 7. A feature went into
error; the next `Update` raised no exception at all and put up

    Diagnostic de la mise a jour : Part1
    [Fermer] [Editer] [Desactiver] [Isoler] [Supprimer]
    [Mettre a niveau] [Mettre a niveau : tout] [Sous-elements...]

It is modal, so it holds COM. Heartbeats stopped, the device went offline, and
every remaining call in the run failed with "no CATIA workstation is
connected" -- a message that is true and describes none of what happened. The
seat stayed dead until a human clicked, which is exactly incident D11 and the
unknown-command box again, in a third costume.

Three decisions here, each of which could have gone wrong quietly:

* **Recognised by the document name in its title**, not by its wording.
  `Diagnostic de la mise a jour` is French and `Update Diagnosis` is English;
  the document name is ours, because we opened it, and is identical on every
  language install. Same trick as the command box echoing the command.
* **Closed with Escape, not with a button.** The daemon has no button-role
  table -- that lives on the server -- and choosing by position on a box whose
  first button happens to be `Fermer` is a guess that one day presses
  `Supprimer`. Escape cancels a modal and can never invoke an action.
* **Closing changes nothing about the part.** Every other button on that box
  edits the tree. The feature stays in error, which is true, and the caller is
  told so in words it can act on.

Offline: fake dialogs with the same surface, no CATIA.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge"
sys.path.insert(0, str(BRIDGE.parent))

from catia_bridge.catia_com import CatiaCom  # noqa: E402

SESSION_SOURCE = (BRIDGE / "session.py").read_text(encoding="utf-8")
COM_SOURCE = (BRIDGE / "catia_com.py").read_text(encoding="utf-8")

recognises = CatiaCom._is_update_diagnosis_box


class _Control:
    def __init__(self, label: str, *, is_field: bool = False) -> None:
        self.label = label
        self.value = None
        self._is_field = is_field


class _Dialog:
    """The surface `ui.active_dialog` returns."""

    def __init__(self, title: str, buttons: list[str], fields: list[str] | None = None) -> None:
        self.title = title
        self.handle = 1
        self.controls = [_Control(name) for name in buttons] + [
            _Control(name, is_field=True) for name in (fields or [])
        ]

    def buttons(self) -> list[_Control]:
        return [c for c in self.controls if not c._is_field]

    def fields(self) -> list[_Control]:
        return [c for c in self.controls if c._is_field]


SEAT = _Dialog(
    "Diagnostic de la mise à jour : Part1",
    ["Fermer", "Editer", "Désactiver", "Isoler", "Supprimer", "Mettre à niveau"],
)


class TestRecognisingIt:
    def test_the_box_measured_on_the_seat(self) -> None:
        assert recognises(SEAT, "Part1") is True

    def test_an_english_seat_is_the_same_box(self) -> None:
        """The wording is translated; the document name is not."""
        english = _Dialog(
            "Update Diagnosis: Part1", ["Close", "Edit", "Deactivate", "Isolate", "Delete"]
        )
        assert recognises(english, "Part1") is True

    def test_a_box_about_a_different_document_is_not_ours(self) -> None:
        assert recognises(SEAT, "Wall bracket") is False

    def test_a_dialog_with_input_fields_is_a_real_command(self) -> None:
        """A working command dialog is the one thing that must never be
        dismissed: it might be a save prompt with a filename in it."""
        pad = _Dialog("Pad Definition: Part1", ["OK", "Cancel", "Preview"], fields=["Length"])
        assert recognises(pad, "Part1") is False

    def test_a_two_button_prompt_is_not_it(self) -> None:
        """A save-or-discard prompt has two buttons and a real decision behind
        it. The diagnosis box has a row of them."""
        save = _Dialog("Part1", ["Oui", "Non"])
        assert recognises(save, "Part1") is False

    def test_no_dialog_is_not_a_dialog(self) -> None:
        assert recognises(None, "Part1") is False

    def test_an_unnamed_document_matches_nothing(self) -> None:
        """Otherwise an empty string folds into every title there is."""
        assert recognises(SEAT, "") is False


class TestHowItIsClosed:
    def test_escape_is_what_is_pressed(self) -> None:
        """Not a button. On a box carrying Delete and Deactivate, choosing by
        position is a guess with a destructive branch."""
        body = _method_source("_dismiss_update_diagnosis_box")
        assert 'press_key(dialog.handle, "Escape")' in body

    def test_no_button_that_acts_can_be_pressed(self) -> None:
        """The fallback matches close words only -- never the first button,
        never an index."""
        body = _method_source("_dismiss_update_diagnosis_box")
        assert "_CLOSE_WORDS" in body
        assert "buttons()[0]" not in body

    def test_the_close_words_carry_the_languages_this_seat_might_be(self) -> None:
        from catia_bridge.catia_com import _CLOSE_WORDS

        assert {"fermer", "close"} <= _CLOSE_WORDS

    def test_no_close_word_is_a_destructive_action(self) -> None:
        """The one assertion that stops this becoming a way to delete work."""
        from catia_bridge.catia_com import _CLOSE_WORDS

        destructive = {"supprimer", "delete", "desactiver", "deactivate", "isoler", "isolate"}
        assert not (_CLOSE_WORDS & destructive)


class TestWhenItRuns:
    def test_the_wedged_path_tries_the_rescue(self) -> None:
        """A helper nothing calls clears nothing."""
        node = _function(SESSION_SOURCE, "_check_alive")
        assert "_clear_a_dialog_we_can_own()" in node

    def test_the_rescue_never_raises(self) -> None:
        """It runs on a path that is already failing. A rescue that throws
        replaces an actionable error with an unrelated one."""
        node = _function(SESSION_SOURCE, "_clear_a_dialog_we_can_own")
        assert "except Exception" in node
        assert "return False" in node

    def test_the_caller_is_told_what_to_do_next(self) -> None:
        """"It has been closed" alone leaves the agent to retry the same call
        against a part that still will not rebuild."""
        node = _function(SESSION_SOURCE, "_check_alive")
        assert "catia_list_features" in node
        assert "in error" in node

    def test_a_backend_without_the_helper_is_not_an_error(self) -> None:
        """The mock has no Win32 layer at all, and the daemon runs against it
        in every test."""
        node = _function(SESSION_SOURCE, "_clear_a_dialog_we_can_own")
        assert "getattr(self.backend" in node


def _function(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
    )
    return ast.get_source_segment(source, node) or ""


def _method_source(name: str) -> str:
    return _function(COM_SOURCE, name)


@pytest.mark.parametrize("name", ["_is_update_diagnosis_box", "_dismiss_update_diagnosis_box"])
def test_both_halves_exist(name: str) -> None:
    assert hasattr(CatiaCom, name)


def _unused(value: Any) -> None:  # pragma: no cover
    return None
