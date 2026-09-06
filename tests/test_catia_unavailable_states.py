"""Why CATIA is unavailable has three answers, not two.

Measured on the seat, 2026-09-06. The model was refused `catia_sketch_create`
("the sketch 'Esquisse.1' is still open"), and instead of calling the
`catia_sketch_close` tool it passed the string "Close Sketch" to
`catia_run_command`. CATIA does not know that command name, so it raised its own
modal box -- `Entrée clavier: Commande inconnue : Close Sketch` -- which holds
COM. Heartbeats stopped, `_resolve_connection` found no live device, and the
message it produced said CATIA "is not running".

It was running. The user was then told to start a bridge that was already
started, on a machine where CATIA was already open, and the run could not
recover: nothing dismisses a dialog nobody has been told about.

The interactive tools that *can* clear it are in `OUT_OF_BAND_TOOLS` precisely
so they work while COM is blocked. They are useless if the failure never points
at them. These tests pin that it does.

Offline: no database, no bridge, no CATIA.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest

from app.catia import dispatch, local_bridge
from app.catia.dispatch import CatiaUnavailable


@pytest.fixture
def offline_bridge(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A single-machine install with no connected device, and no daemon to start."""
    monkeypatch.setattr(dispatch, "_online", lambda db, user_id: None)
    monkeypatch.setattr(local_bridge, "ensure_started", lambda db, user_id, wait_s=0.0: False)
    monkeypatch.setattr(dispatch.local_bridge, "ensure_started", lambda db, u, wait_s=0.0: False)
    monkeypatch.setattr(dispatch.local_bridge, "is_supported", lambda: True)
    monkeypatch.setattr(dispatch.local_bridge, "last_error", lambda user_id=None: None)
    yield


def _message(monkeypatch: pytest.MonkeyPatch, *, catia_running: bool | None) -> str:
    monkeypatch.setattr(
        dispatch.local_bridge, "catia_process_is_running", lambda: catia_running
    )
    with pytest.raises(CatiaUnavailable) as caught:
        dispatch._resolve_connection(None, "user-1")  # type: ignore[arg-type]
    return str(caught.value)


def test_catia_running_but_silent_names_the_dialog_and_not_a_restart(
    offline_bridge: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state that cost a session. CATIA is up; a dialog is holding COM."""
    message = _message(monkeypatch, catia_running=True)

    assert "modal dialog" in message
    assert "catia_describe_dialog" in message
    assert "catia_dialog_action" in message
    # The two instructions that were actively wrong must not appear.
    assert "open_in_catia" not in message, "telling it to start what is already running"
    assert "start the Kryova CATIA bridge" not in message
    assert "is not running" not in message


def test_catia_genuinely_absent_still_says_to_start_it(
    offline_bridge: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original message is still right when it is actually right."""
    message = _message(monkeypatch, catia_running=False)

    assert "is not running" in message
    assert "open_in_catia" in message
    assert "modal dialog" not in message


def test_an_unknowable_process_state_does_not_claim_a_dialog(
    offline_bridge: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`None` means "could not tell", and must not be read as "running".

    Inventing a dialog that is not there would send the agent hunting for
    something to dismiss, which is the mirror image of the bug being fixed.
    """
    message = _message(monkeypatch, catia_running=None)

    assert "modal dialog" not in message
    assert "open_in_catia" in message


def test_a_known_reason_is_reported_verbatim_and_beats_process_guessing(
    offline_bridge: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded startup failure is better evidence than a process check."""
    monkeypatch.setattr(
        dispatch.local_bridge, "last_error", lambda user_id=None: "the token was rejected"
    )
    message = _message(monkeypatch, catia_running=True)

    assert "the token was rejected" in message
    assert "modal dialog" not in message


class TestProcessProbe:
    """`catia_process_is_running` itself."""

    def test_a_miss_is_false_not_a_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tasklist prints "INFO: No tasks are running..." and exits 0 on a miss."""
        monkeypatch.setattr(local_bridge.sys, "platform", "win32")
        monkeypatch.setattr(
            local_bridge.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 0, "INFO: No tasks are running which match the specified criteria.", ""
            ),
        )
        assert local_bridge.catia_process_is_running() is False

    def test_a_hit_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_bridge.sys, "platform", "win32")
        monkeypatch.setattr(
            local_bridge.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 0, "CNEXT.exe                     8452 Console    1    1,234,000 K", ""
            ),
        )
        assert local_bridge.catia_process_is_running() is True

    def test_a_failing_probe_says_it_does_not_know(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never False on an error: that is the claim that caused the bug."""
        monkeypatch.setattr(local_bridge.sys, "platform", "win32")

        def explode(*_a: object, **_k: object) -> None:
            raise OSError("tasklist is not on PATH")

        monkeypatch.setattr(local_bridge.subprocess, "run", explode)
        assert local_bridge.catia_process_is_running() is None

    def test_off_windows_it_says_it_does_not_know(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(local_bridge.sys, "platform", "linux")
        assert local_bridge.catia_process_is_running() is None
