"""Driving CATIA's own interface: any command, any dialog, in any language.

The tools these cover are the ones that reach the whole of CATIA rather than the
thirty operations Kryova implements directly. They work by reading the live
interface and pressing it, which makes two things testable that were not
testable before and one thing that still is not.

**Testable, and tested here:** the whole loop. Select, run the command, read the
dialog it opened, fill a field by the label the dialog actually shows, press OK,
get a feature. The mock interface (`mock_ui.py`) is wired to the mock part, so
pressing OK on a Pad dialog really does produce a Pad and the mass really does
change -- which means a test can assert the *outcome* rather than that a
function was called.

**Testable, and the reason the mock has a language:** that none of it depends on
English. Every test below runs against a German seat as well as an English one.
A German CATIA calls Pad `Block`, its dialog `Block Definition`, its length
field `Länge` and its accept button `OK` but its cancel button `Abbrechen` -- so
a hardcoded label anywhere in the chain fails here rather than on a customer's
workstation.

**Not testable from Linux, and not pretended otherwise:** whether CATIA's real
dialogs answer `WM_GETTEXT`, whether they need the `EN_CHANGE` notification,
what their window classes are. `ui_automation.py` is exercised only for the
parts that are pure logic (label-to-field pairing, language detection from a
menu bar, the refusal policy). The rest is listed in the setup doc as what a
Windows session verifies, and `describe_dialog` reports unrecognised widgets
with their window class so that session is productive rather than a guessing
game.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.catia_kb.ui import (
    ButtonRole,
    button_labels,
    forbidden_reason,
    resolve_command,
    resolve_workbench,
    role_of,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge import ui_automation as ui  # noqa: E402
from catia_bridge import ui_policy  # noqa: E402
from catia_bridge.backend import OUT_OF_BAND_TOOLS, TOOL_METHODS  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.mock_ui import MockUi  # noqa: E402
from catia_bridge.session import BridgeSession  # noqa: E402
from catia_bridge.tool_table import TOOLS  # noqa: E402

#: Every test that could plausibly depend on the interface language runs twice.
#: German is the useful second case: it translates every one of Pad, the dialog
#: title, the field labels and the Cancel button, while leaving OK alone -- so
#: it catches both "assumed the English word" and "assumed every word differs".
LANGUAGES = ("en", "de")

#: What each seat calls the things these tests drive.
SEAT = {
    "en": {"pad": "Pad", "title": "Pad Definition", "length": "Length", "cancel": "Cancel"},
    "de": {"pad": "Block", "title": "Block Definition", "length": "Länge", "cancel": "Abbrechen"},
}


def make_session(tmp_path: Path, language: str) -> BridgeSession:
    backend = MockCatia(tmp_path / f"catia-{language}", language=language)
    sent: list[dict] = []
    session = BridgeSession(backend, bridge_version="1.0.0", hostname="WS-TEST", send=sent.append)
    session.sent = sent  # type: ignore[attr-defined]
    session.backend_mock = backend  # type: ignore[attr-defined]
    return session


def call(session, tool: str, arguments: dict | None = None, **extra) -> dict:
    """One frame in, one frame out -- the real daemon path, schemas and all."""
    session.handle_frame(
        json.dumps(
            {
                "type": "call",
                "id": f"call-{len(session.sent)}",
                "tool": tool,
                "conversation_id": "conv-1",
                "arguments": arguments or {},
                **extra,
            }
        )
    )
    return session.sent[-1]


def run_command(session, command: str, language: str) -> dict:
    """Call `catia_run_command` the way the server does, resolution and all.

    Going through `resolve_command` rather than hand-writing the candidates is
    the point: it is the server-side translation step, and a test that skipped
    it would pass with the translation table empty.
    """
    target = resolve_command(command, language=language)
    return call(
        session,
        "catia_run_command",
        {
            "command": command,
            "candidates": list(target.candidates),
            "command_name": target.name,
            "command_key": target.key or "",
        },
    )


def press(session, action: str, language: str) -> dict:
    role = ButtonRole(action)
    return call(
        session,
        "catia_dialog_action",
        {"action": action, "labels": list(button_labels(role, language))},
    )


def build_profile(session) -> None:
    assert call(session, "catia_new_part", {"name": "Bracket"})["ok"]
    assert call(
        session, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 40}
    )["ok"]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class TestTheInteractiveLoop:
    @pytest.mark.parametrize("language", LANGUAGES)
    def test_a_command_a_dialog_a_field_and_ok_builds_the_feature(self, tmp_path, language):
        """The whole point, end to end: press Pad, type 25, press OK, get a Pad."""
        session = make_session(tmp_path, language)
        build_profile(session)

        started = run_command(session, "Pad", language)
        assert started["ok"], started
        assert started["data"]["dialog_open"] is True
        # The command has not run yet, and the result says so rather than
        # letting the model believe a Pad exists.
        assert "not" in started["data"]["next"] or "until" in started["data"]["next"]
        assert call(session, "catia_list_features")["data"]["features"] == [
            {"name": "Sketch.1", "type": "Sketch"}
        ]

        described = call(session, "catia_describe_dialog")["data"]
        assert described["dialog_open"] is True
        assert described["title"] == SEAT[language]["title"]
        labels = [field["label"] for field in described["fields"]]
        assert SEAT[language]["length"] in labels

        filled = call(
            session,
            "catia_fill_dialog",
            {"fields": [{"name": SEAT[language]["length"], "value": "25mm"}]},
        )
        assert filled["ok"], filled

        confirmed = press(session, "ok", language)
        assert confirmed["ok"], confirmed
        assert confirmed["data"]["dialog_open"] is False
        assert confirmed["data"]["feature"] == "Pad.1"

        # The dimension typed into the dialog is the one that got built:
        # 60 x 40 x 25 mm of steel is 0.471 kg, and a stubbed OK could not
        # produce that number.
        assert call(session, "catia_measure")["data"]["mass_kg"] == pytest.approx(0.471, abs=1e-3)

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_cancel_changes_nothing(self, tmp_path, language):
        session = make_session(tmp_path, language)
        build_profile(session)
        run_command(session, "Pad", language)
        call(session, "catia_fill_dialog", {"fields": [{"name": SEAT[language]["length"], "value": "99mm"}]})

        cancelled = press(session, "cancel", language)
        assert cancelled["ok"], cancelled
        assert cancelled["data"]["dialog_open"] is False
        assert cancelled["data"]["pressed"] == SEAT[language]["cancel"]
        assert call(session, "catia_list_features")["data"]["features"] == [
            {"name": "Sketch.1", "type": "Sketch"}
        ]

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_escape_abandons_the_dialog_like_a_keyboard_would(self, tmp_path, language):
        session = make_session(tmp_path, language)
        build_profile(session)
        run_command(session, "Pad", language)
        assert call(session, "catia_press_key", {"key": "escape"})["data"]["dialog_open"] is False
        assert len(call(session, "catia_list_features")["data"]["features"]) == 1

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_enter_confirms_it(self, tmp_path, language):
        session = make_session(tmp_path, language)
        build_profile(session)
        run_command(session, "Pad", language)
        assert call(session, "catia_press_key", {"key": "enter"})["data"]["dialog_open"] is False
        assert call(session, "catia_list_features")["data"]["features"][-1]["type"] == "Pad"

    def test_selecting_a_sketch_is_what_the_dialog_pads(self, tmp_path):
        """`catia_select` then a command is the flow an engineer actually uses."""
        session = make_session(tmp_path, "en")
        assert call(session, "catia_new_part", {"name": "Two"})["ok"]
        call(session, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 40})
        call(session, "catia_sketch_circle", {"plane": "XY", "diameter_mm": 20})

        assert call(session, "catia_select", {"features": ["Sketch.1"]})["data"]["count"] == 1
        run_command(session, "Pad", "en")
        press(session, "ok", "en")
        # Sketch.1 is the rectangle; padding the circle instead would give a
        # different mass, so this asserts the selection was honoured.
        assert call(session, "catia_measure")["data"]["mass_kg"] == pytest.approx(0.188, abs=1e-3)

    def test_selecting_something_that_is_not_there_says_so(self, tmp_path):
        session = make_session(tmp_path, "en")
        call(session, "catia_new_part", {"name": "Empty"})
        answer = call(session, "catia_select", {"features": ["Sketch.7"]})
        assert answer["ok"] is False
        assert "Sketch.7" in answer["error"]
        assert "catia_list_features" in answer["error"]

    def test_an_empty_selection_clears_it(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        call(session, "catia_select", {"features": ["Sketch.1"]})
        assert call(session, "catia_select", {"features": []})["data"]["count"] == 0

    def test_two_commands_at_once_is_refused_rather_than_stacked(self, tmp_path):
        """A second dialog on top of the first is how a session gets wedged."""
        session = make_session(tmp_path, "en")
        build_profile(session)
        run_command(session, "Pad", "en")
        second = run_command(session, "Pocket", "en")
        assert second["ok"] is False
        assert "already open" in second["error"]
        assert "catia_dialog_action" in second["error"]

    def test_filling_a_field_that_does_not_exist_lists_the_ones_that_do(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        run_command(session, "Pad", "en")
        answer = call(session, "catia_fill_dialog", {"fields": [{"name": "Thickness", "value": "3mm"}]})
        assert answer["ok"] is False
        assert "Thickness" in answer["error"]
        assert "Length" in answer["error"]

    def test_filling_with_no_dialog_open_says_to_run_the_command_first(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        answer = call(session, "catia_fill_dialog", {"fields": [{"name": "Length", "value": "5mm"}]})
        assert answer["ok"] is False
        assert "catia_run_command" in answer["error"]

    def test_describe_reports_no_dialog_rather_than_failing(self, tmp_path):
        """It is also the way to check whether a command finished on its own."""
        session = make_session(tmp_path, "en")
        answer = call(session, "catia_describe_dialog")
        assert answer["ok"] is True
        assert answer["data"]["dialog_open"] is False

    def test_a_dropdown_only_accepts_its_own_options(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        run_command(session, "Pad", "en")
        answer = call(session, "catia_fill_dialog", {"fields": [{"name": "Type", "value": "Blind"}]})
        assert answer["ok"] is False
        assert "Up to last" in answer["error"]

    def test_a_checkbox_takes_a_word_not_a_number(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        run_command(session, "Pad", "en")
        filled = call(
            session, "catia_fill_dialog", {"fields": [{"name": "Mirrored extent", "value": "true"}]}
        )
        assert filled["ok"], filled
        checkbox = next(
            f for f in filled["data"]["dialog"]["fields"] if f["label"] == "Mirrored extent"
        )
        assert checkbox["checked"] is True

    def test_preview_does_not_commit(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        run_command(session, "Pad", "en")
        previewed = press(session, "preview", "en")
        assert previewed["ok"], previewed
        assert previewed["data"]["dialog_open"] is True
        assert call(session, "catia_list_features")["data"]["features"] == [
            {"name": "Sketch.1", "type": "Sketch"}
        ]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    @pytest.mark.parametrize("language", LANGUAGES)
    def test_the_menu_is_reported_in_the_seats_own_words(self, tmp_path, language):
        session = make_session(tmp_path, language)
        build_profile(session)
        listed = call(session, "catia_list_commands")["data"]
        labels = {item["command"] for item in listed["commands"]}
        assert SEAT[language]["pad"] in labels
        paths = {item["menu"] for item in listed["commands"]}
        assert any(SEAT[language]["pad"] in path for path in paths)

    def test_search_narrows_it(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        listed = call(session, "catia_list_commands", {"search": "fillet"})["data"]
        assert [item["command"] for item in listed["commands"]] == ["Edge Fillet"]

    def test_a_greyed_command_is_listed_as_unavailable_not_omitted(self, tmp_path):
        """"It is there but greyed out" is a different answer from "it is not there".

        The first tells the agent to select something; the second tells it the
        command does not exist and to give up. Dropping disabled items would
        turn every one of the first into one of the second.
        """
        session = make_session(tmp_path, "en")
        call(session, "catia_new_part", {"name": "Empty"})
        listed = call(session, "catia_list_commands", {"search": "Edge Fillet"})["data"]
        entry = next(item for item in listed["commands"] if item["command"] == "Edge Fillet")
        assert entry["available"] is False

    def test_running_a_greyed_command_explains_why_rather_than_silently_failing(self, tmp_path):
        session = make_session(tmp_path, "en")
        call(session, "catia_new_part", {"name": "Empty"})
        answer = run_command(session, "Edge Fillet", "en")
        assert answer["ok"] is False
        assert "solid" in answer["error"]

    def test_an_unknown_command_names_the_discovery_tool(self, tmp_path):
        session = make_session(tmp_path, "en")
        build_profile(session)
        answer = call(
            session,
            "catia_run_command",
            {"command": "Teleport", "candidates": ["Teleport"], "command_name": "Teleport"},
        )
        assert answer["ok"] is False
        assert "catia_list_commands" in answer["error"]

    def test_switching_workbench_reports_the_licence_it_needs(self, tmp_path):
        session = make_session(tmp_path, "en")
        target = resolve_workbench("Generative Shape Design", language="en")
        answer = call(
            session,
            "catia_switch_workbench",
            {
                "workbench": "Generative Shape Design",
                "workbench_id": target.workbench_id,
                "workbench_name": target.name,
                "menu_path": list(target.menu_path),
                "licence": target.licence,
            },
        )
        assert answer["ok"], answer
        assert answer["data"]["workbench"] == "Generative Shape Design"
        assert "GSD" in answer["data"]["licence"]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestRefusals:
    @pytest.mark.parametrize(
        "command",
        ["Options", "Customize", "Save As", "Exit", "Macros", "Visual Basic Editor"],
    )
    def test_the_daemon_refuses_what_no_checkpoint_could_undo(self, tmp_path, command):
        session = make_session(tmp_path, "en")
        build_profile(session)
        answer = call(
            session,
            "catia_run_command",
            {"command": command, "candidates": [command], "command_name": command},
        )
        assert answer["ok"] is False
        assert "does not drive" in answer["error"]

    @pytest.mark.parametrize(
        "command", ["Optionen", "Personnaliser", "Speichern unter", "Guardar como", "Makro"]
    )
    def test_it_refuses_them_in_the_other_languages_too(self, tmp_path, command):
        """A refusal that only worked in English would be no refusal at all.

        The candidate list is what a non-English seat receives, so a deny list
        that only knew the English names would wave through the German label of
        the same command.
        """
        session = make_session(tmp_path, "en")
        answer = call(
            session,
            "catia_run_command",
            {"command": command, "candidates": [command], "command_name": command},
        )
        assert answer["ok"] is False
        assert "does not drive" in answer["error"]

    def test_a_forbidden_candidate_anywhere_in_the_list_refuses_the_call(self, tmp_path):
        """Checking only the first candidate would leave the rest as a way past."""
        session = make_session(tmp_path, "en")
        answer = call(
            session,
            "catia_run_command",
            {"command": "Pad", "candidates": ["Pad", "Macros"], "command_name": "Pad"},
        )
        assert answer["ok"] is False
        assert "does not drive" in answer["error"]

    @pytest.mark.parametrize(
        "command",
        [
            # Each of these was refused by an earlier version of the rule, and
            # each is a command an engineer uses without thinking about it.
            "Exit Sketcher Workbench",  # `exit` matched as a leading word
            "Copy Options",  # `options` matched as a substring
            "Optional Rib",  # `options` again, on a word that merely starts alike
            "Save",  # not `Save As`; saving where the daemon chose is fine
            "Macro Definition Analysis",  # begins with `macro`, runs no code
        ],
    )
    def test_it_does_not_refuse_things_that_merely_contain_a_forbidden_word(self, command):
        """A refusal that over-reaches teaches the agent the tool is unreliable.

        That is worse than it sounds: the agent's recovery from a refusal is to
        try something else, so a wrongly refused command becomes a wrongly built
        part rather than an error anybody sees.
        """
        assert forbidden_reason(command) is None
        assert ui_policy.refusal(command) is None

    def test_both_sides_of_the_wire_agree_on_what_is_forbidden(self):
        """The server's tables and the daemon's are two copies and must match.

        The daemon checks last and therefore wins, but a divergence means the
        server would resolve a command it believes is fine into candidates the
        daemon then refuses -- a confusing failure rather than a clear one.
        """
        from app.catia_kb.ui import FORBIDDEN_EXACT, FORBIDDEN_PREFIX

        assert FORBIDDEN_EXACT == ui_policy.FORBIDDEN_EXACT
        assert FORBIDDEN_PREFIX == ui_policy.FORBIDDEN_PREFIX

    def test_the_two_tables_do_not_overlap(self):
        """A label in both would be matched by whichever rule ran first.

        Harmless today because the reasons agree, and worth pinning: the split
        only stays comprehensible while each label belongs to exactly one rule.
        """
        from app.catia_kb.ui import FORBIDDEN_EXACT, FORBIDDEN_PREFIX

        assert not set(FORBIDDEN_EXACT) & set(FORBIDDEN_PREFIX)


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------


class TestLanguage:
    def test_a_command_resolves_to_the_seats_label_first(self):
        target = resolve_command("Pad", language="de")
        assert target.candidates[0] == "Block"
        assert "Pad" in target.candidates  # English stays as the fallback

    def test_an_untranslated_command_says_so_instead_of_inventing_one(self):
        """The honesty rule, at the point where breaking it would silently fail.

        `StartCommand` ignores a name it does not know without raising, so an
        invented German label produces "success" and no geometry. Saying the
        translation is missing is what lets the agent read the live menu
        instead.
        """
        target = resolve_command("Isolate", language="de")
        if not target.translated:
            assert "no de label recorded" in target.note
            assert target.candidates == ("Isolate",)

    def test_an_unknown_command_is_passed_through_rather_than_refused(self):
        target = resolve_command("Some Command We Never Catalogued")
        assert target.key is None
        assert target.candidates == ("Some Command We Never Catalogued",)
        assert "not in the Kryova CATIA reference" in target.note

    def test_an_ambiguous_command_reports_the_alternatives(self):
        """Flange is an SMD command and an Aerospace Sheet Metal one.

        Pressing the wrong one builds the wrong part on a real aircraft
        component, so the alternatives ride along in the result instead of one
        being chosen silently.
        """
        target = resolve_command("Flange")
        assert target.alternatives

    @pytest.mark.parametrize(
        ("label", "role"),
        [
            ("OK", ButtonRole.OK),
            ("Aceptar", ButtonRole.OK),
            ("Annuler", ButtonRole.CANCEL),
            ("Abbrechen", ButtonRole.CANCEL),
            ("Annulla", ButtonRole.CANCEL),
            ("Cancelar", ButtonRole.CANCEL),
            ("Anwenden", ButtonRole.APPLY),
            ("Aperçu", ButtonRole.PREVIEW),
            ("Sì", ButtonRole.YES),
            ("Nein", ButtonRole.NO),
            ("Schließen", ButtonRole.CLOSE),
        ],
    )
    def test_a_button_label_maps_to_its_role_in_every_language(self, label, role):
        assert role_of(label) is role

    def test_an_unknown_button_label_has_no_role(self):
        """`None` is correct: `Reverse Direction` is a button, not an action."""
        assert role_of("Reverse Direction") is None

    def test_the_seats_language_is_tried_before_english(self):
        assert button_labels(ButtonRole.CANCEL, "de")[0] == "Abbrechen"
        assert button_labels(ButtonRole.OK, "es")[0] == "Aceptar"

    def test_every_role_still_offers_english_when_the_language_is_unknown(self):
        """A seat in Japanese gets the whole table rather than nothing."""
        for role in ButtonRole:
            labels = button_labels(role, "ja")
            assert labels
            assert set(labels) >= set(button_labels(role, "en"))

    def test_the_daemon_reports_which_language_it_is_running_in(self, tmp_path):
        for language in LANGUAGES:
            session = make_session(tmp_path, language)
            assert session.hello_frame()["ui_language"] == language

    @pytest.mark.parametrize("language", ["en", "fr", "de", "it", "es"])
    def test_a_menu_bar_identifies_its_language(self, language):
        titles = ui.MENU_BAR_LANGUAGES[language]
        items = [ui.MenuItem(label=title, path=(title,)) for title in titles]
        assert ui.detect_language(items) == language

    def test_an_unrecognised_menu_bar_reports_no_language_rather_than_guessing(self):
        """A Japanese seat must come back empty, not as the nearest match.

        Empty is handled: commands go out under their English names and the
        daemon finds the real label by reading this same menu. A wrong guess
        would send a German label to a Japanese CATIA, which ignores it in
        silence.
        """
        items = [ui.MenuItem(label=label, path=(label,)) for label in ("ファイル", "編集", "表示")]
        assert ui.detect_language(items) == ""

    def test_one_coincidental_match_is_not_a_detection(self):
        """`File` alone is English and Italian; one hit cannot decide."""
        assert ui.detect_language([ui.MenuItem(label="File", path=("File",))]) == ""


# ---------------------------------------------------------------------------
# The wiring that makes a stuck dialog recoverable
# ---------------------------------------------------------------------------


class TestOutOfBand:
    def test_the_dialog_tools_bypass_the_com_liveness_probe(self, tmp_path):
        """The bug this prevents: a stuck dialog with no way out but a human.

        `_ensure_alive` asks CATIA's automation server whether it is responding,
        and a modal dialog is exactly when it is not. If the tools that dismiss
        a dialog were gated on that probe, they could only run when no dialog
        was stuck -- and the session would need someone to walk over to the
        workstation.
        """
        session = make_session(tmp_path, "en")
        backend = session.backend_mock  # type: ignore[attr-defined]
        build_profile(session)
        run_command(session, "Pad", "en")

        def wedged() -> None:
            raise AssertionError("health() must not be called for an out-of-band tool")

        backend.health = wedged  # type: ignore[method-assign]

        assert call(session, "catia_describe_dialog")["ok"]
        assert call(session, "catia_fill_dialog", {"fields": [{"name": "Length", "value": "12mm"}]})["ok"]
        assert call(session, "catia_list_commands", {"search": "Pad"})["ok"]
        assert press(session, "cancel", "en")["ok"]

    def test_an_ordinary_tool_still_fails_fast_on_a_wedged_catia(self, tmp_path):
        """The probe is right for everything else and must stay."""
        session = make_session(tmp_path, "en")
        backend = session.backend_mock  # type: ignore[attr-defined]
        build_profile(session)

        def wedged() -> None:
            raise RuntimeError("CATIA is showing a modal dialog")

        backend.health = wedged  # type: ignore[method-assign]
        answer = call(session, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
        assert answer["ok"] is False

    def test_every_out_of_band_tool_exists_and_is_read_or_dialog_work(self):
        for tool in OUT_OF_BAND_TOOLS:
            assert tool in TOOLS
            assert tool in TOOL_METHODS

    def test_the_interactive_tools_are_not_auto_checkpointed(self):
        """A checkpoint is a COM save, and COM is blocked when they run.

        Requiring one would refuse the call -- `_auto_checkpoint` raises rather
        than proceeding without a snapshot -- so the tools that unwedge a
        session could never run on a wedged session.
        """
        from app.catia.dispatch import _NO_AUTO_CHECKPOINT

        for tool in ("catia_fill_dialog", "catia_dialog_action", "catia_press_key"):
            assert tool in _NO_AUTO_CHECKPOINT
        # The command runner is the exception and must stay checkpointed: it is
        # the one that starts something, and COM is alive by definition when it
        # does.
        assert "catia_run_command" not in _NO_AUTO_CHECKPOINT


# ---------------------------------------------------------------------------
# The Win32 layer's pure logic
# ---------------------------------------------------------------------------


class TestWin32Logic:
    """The parts of `ui_automation` that are logic rather than Win32 calls.

    Everything that talks to `user32` needs Windows and a live CATIA, and is
    left to a Windows session rather than mocked into a test that would prove
    only that the mock matches itself.
    """

    def test_it_imports_on_linux_and_refuses_rather_than_crashing(self):
        assert ui.AVAILABLE is (sys.platform == "win32")
        if not ui.AVAILABLE:
            with pytest.raises(ui.UiUnavailable):
                ui.main_window()

    def test_a_field_takes_the_label_to_its_left(self):
        """Which is how a Win32 dialog labels a box: with a separate Static."""
        controls = [
            ui.Control(kind="label", label="Length", rect=(0, 10, 50, 26)),
            ui.Control(kind="text", label="", value="10mm", rect=(60, 10, 160, 26)),
        ]
        paired = ui._label_fields(controls)
        assert paired[1].label == "Length"

    def test_a_same_row_label_beats_one_on_the_line_above(self):
        controls = [
            ui.Control(kind="label", label="First limit", rect=(0, 0, 80, 16)),
            ui.Control(kind="label", label="Length", rect=(0, 20, 50, 36)),
            ui.Control(kind="text", label="", value="10mm", rect=(60, 20, 160, 36)),
        ]
        paired = ui._label_fields(controls)
        assert paired[2].label == "Length"

    def test_a_trailing_colon_is_not_part_of_the_name(self):
        controls = [
            ui.Control(kind="label", label="Radius:", rect=(0, 0, 50, 16)),
            ui.Control(kind="text", label="", rect=(60, 0, 160, 16)),
        ]
        assert ui._label_fields(controls)[1].label == "Radius"

    def test_a_field_with_no_label_nearby_keeps_none(self):
        """Better an unnamed field the agent can see than a wrong name."""
        controls = [
            ui.Control(kind="label", label="Elsewhere", rect=(0, 500, 50, 516)),
            ui.Control(kind="text", label="", rect=(60, 0, 160, 16)),
        ]
        assert ui._label_fields(controls)[1].label == ""

    def test_a_dialog_separates_its_fields_from_its_buttons(self):
        dialog = ui.Dialog(
            title="Pad Definition",
            handle=1,
            controls=(
                ui.Control(kind="text", label="Length", value="10mm"),
                ui.Control(kind="button", label="OK", control_id=1),
                ui.Control(kind="button", label="Cancel", control_id=2),
                ui.Control(kind="label", label="ignored"),
            ),
        )
        described = dialog.describe()
        assert [f["label"] for f in described["fields"]] == ["Length"]
        assert described["buttons"] == ["OK", "Cancel"]

    def test_the_key_list_is_closed(self):
        """An open keystroke channel is a bigger surface than it looks."""
        assert set(ui.KEYS) == {
            "enter",
            "escape",
            "tab",
            "delete",
            "space",
            "up",
            "down",
            "left",
            "right",
            "home",
            "end",
        }

    def test_a_menu_item_walks_its_own_subtree(self):
        tree = ui.MenuItem(
            label="Insert",
            path=("Insert",),
            children=[
                ui.MenuItem(
                    label="Sketch-Based Features",
                    path=("Insert", "Sketch-Based Features"),
                    children=[
                        ui.MenuItem(
                            label="Pad", path=("Insert", "Sketch-Based Features", "Pad"), command_id=7
                        )
                    ],
                )
            ],
        )
        found = ui.find_menu_item([tree], lambda item: item.label == "Pad")
        assert found is not None
        assert found.command_id == 7
        assert found.path == ("Insert", "Sketch-Based Features", "Pad")


# ---------------------------------------------------------------------------
# The mock interface itself
# ---------------------------------------------------------------------------


class TestMockUi:
    def test_an_unknown_command_is_ignored_silently_as_catia_ignores_it(self):
        """`StartCommand` does not raise on a name it does not know.

        The mock reproduces that silence rather than helpfully raising, because
        detecting it is the tool layer's job and a mock that raised would let a
        missing detection pass.
        """
        assert MockUi().start_command("Nonexistent Command") is False

    def test_it_speaks_the_language_it_was_built_with(self):
        assert MockUi(language="de").say("Pad") == "Block"
        assert MockUi(language="fr").say("Edge Fillet") == "Congé d'arête"
        assert MockUi(language="en").say("Pad") == "Pad"

    def test_an_unknown_language_falls_back_to_english_rather_than_failing(self):
        assert MockUi(language="ja").language == "en"


# ---------------------------------------------------------------------------
# Server-side resolution
# ---------------------------------------------------------------------------


class TestServerResolution:
    def test_the_dispatcher_resolves_a_command_into_seat_labels(self):
        from app.catia.dispatch import _resolve_ui

        payload = _resolve_ui("catia_run_command", {"command": "Pocket"}, "de")
        assert payload["candidates"][0] == "Tasche"
        assert payload["command_name"] == "Pocket"
        assert payload["command_key"] == "part_design.pocket"

    def test_it_resolves_a_button_role_into_seat_labels(self):
        from app.catia.dispatch import _resolve_ui

        payload = _resolve_ui("catia_dialog_action", {"action": "cancel"}, "fr")
        assert payload["labels"][0] == "Annuler"

    def test_a_named_button_is_passed_through_untranslated(self):
        """The model read that label off the dialog; translating it would break it."""
        from app.catia.dispatch import _resolve_ui

        payload = _resolve_ui(
            "catia_dialog_action", {"action": "ok", "button": "Inverser la direction"}, "fr"
        )
        assert "labels" not in payload
        assert payload["button"] == "Inverser la direction"

    def test_no_language_still_resolves_to_english(self):
        from app.catia.dispatch import _resolve_ui

        payload = _resolve_ui("catia_run_command", {"command": "Pad"}, None)
        assert payload["candidates"] == ["Pad"]

    def test_a_hello_frame_without_a_language_is_not_a_failure(self):
        from app.catia.connection import BridgeHello

        hello = BridgeHello.parse(
            {"type": "hello", "catia_version": "V5", "bridge_version": "1", "hostname": "h"}
        )
        assert hello.ui_language == ""

    @pytest.mark.parametrize(
        ("sent", "expected"),
        [("de", "de"), ("DE", "de"), ("de-DE", "de"), ("de_DE", "de"), ("deutsch", ""), ("", "")],
    )
    def test_a_reported_language_is_normalised_not_trusted(self, sent, expected):
        """It is peer-supplied and becomes a lookup key, so it is narrowed first."""
        from app.catia.connection import BridgeHello

        hello = BridgeHello.parse(
            {
                "type": "hello",
                "catia_version": "V5",
                "bridge_version": "1",
                "hostname": "h",
                "ui_language": sent,
            }
        )
        assert hello.ui_language == expected


# ---------------------------------------------------------------------------
# The vocabulary stays in step
# ---------------------------------------------------------------------------


def test_every_interactive_tool_is_on_both_sides_of_the_wire():
    from app.catia.tool_specs import CATIA_TOOL_SPECS

    interactive = {
        "catia_list_commands",
        "catia_run_command",
        "catia_describe_dialog",
        "catia_fill_dialog",
        "catia_dialog_action",
        "catia_press_key",
        "catia_switch_workbench",
        "catia_select",
    }
    server = {spec.name for spec in CATIA_TOOL_SPECS}
    assert interactive <= server
    assert interactive <= set(TOOLS)
    assert interactive <= set(TOOL_METHODS)


def test_the_agent_is_taught_the_loop_rather_than_left_to_infer_it():
    """A tool the prompt never explains is a tool the model calls in the wrong order."""
    from app.ai.prompts import AGENT_SYSTEM_CATIA

    for tool in ("catia_run_command", "catia_describe_dialog", "catia_fill_dialog"):
        assert tool in AGENT_SYSTEM_CATIA
    assert "Never leave a dialog open" in AGENT_SYSTEM_CATIA
