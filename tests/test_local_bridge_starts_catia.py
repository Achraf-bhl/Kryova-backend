"""The product starts CATIA. The customer should never be told to.

The bridge daemon is spawned with `--wait-for-catia`, so on a machine where
CATIA is closed it waits for an application nobody is going to start. Every
CATIA tool then fails with "no workstation is connected", and the assistant's
only honest reply is to ask the user to go and open CATIA -- an instruction
this product should never have to give. `open_in_catia` has been able to
launch it since it was written; it just had to be chosen, and the agent
chooses it only sometimes.

Two things are asserted, and the second matters as much as the first:

* CATIA is started when it is known not to be running;
* it is NOT started when the check could not tell. `catia_process_is_running`
  has three answers on purpose, and starting a second CATIA because we could
  not see the first is worse than doing nothing -- it can leave a splash
  screen holding the display on a machine that was working.

And the launch only starts the application. It creates no document: two COM
clients each creating a part is the defect recorded in `_open_in_catia`, and
every document comes from the daemon.

Offline: the launcher and the probe are both replaced.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.catia import local_bridge

SOURCE = (Path(__file__).resolve().parent.parent / "app" / "catia" / "local_bridge.py").read_text(
    encoding="utf-8"
)


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Record every launch, without one reaching COM."""
    calls: list[bool] = []

    def fake_launch(visible: bool = True) -> Any:
        calls.append(visible)
        return None

    import app.catia.bridge as bridge_module

    monkeypatch.setattr(bridge_module, "launch", fake_launch)
    return calls


def test_it_starts_catia_when_catia_is_not_running(
    monkeypatch: pytest.MonkeyPatch, launches: list[bool]
) -> None:
    monkeypatch.setattr(local_bridge, "catia_process_is_running", lambda: False)
    local_bridge._ensure_catia_is_running()
    assert launches == [True], "CATIA was not started, so the bridge waits for nothing"


def test_it_does_nothing_when_catia_is_already_running(
    monkeypatch: pytest.MonkeyPatch, launches: list[bool]
) -> None:
    monkeypatch.setattr(local_bridge, "catia_process_is_running", lambda: True)
    local_bridge._ensure_catia_is_running()
    assert launches == []


def test_it_does_nothing_when_the_check_could_not_tell(
    monkeypatch: pytest.MonkeyPatch, launches: list[bool]
) -> None:
    """`None` is the third answer and must not be read as False."""
    monkeypatch.setattr(local_bridge, "catia_process_is_running", lambda: None)
    local_bridge._ensure_catia_is_running()
    assert launches == []


def test_a_failed_launch_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every caller is on a path that copes with "no CATIA". A convenience
    that can break the turn is not a convenience."""
    monkeypatch.setattr(local_bridge, "catia_process_is_running", lambda: False)

    import app.catia.bridge as bridge_module

    def explode(visible: bool = True) -> Any:
        raise RuntimeError("CATIA could not be started")

    monkeypatch.setattr(bridge_module, "launch", explode)
    local_bridge._ensure_catia_is_running()  # must not raise


def test_it_is_called_where_the_daemon_is_supervised() -> None:
    """A helper nothing calls is a feature nobody has."""
    tree = ast.parse(SOURCE)
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ensure_started"
    )
    body = ast.get_source_segment(SOURCE, node) or ""
    assert "_ensure_catia_is_running()" in body


def test_it_creates_no_document() -> None:
    """Only `launch`. `new_part` here would be a second COM client creating a
    part beside the daemon's -- the defect `_open_in_catia` was rewritten to
    stop, which cost three seat runs to find."""
    tree = ast.parse(SOURCE)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_ensure_catia_is_running"
    )
    body = "\n".join(
        ast.get_source_segment(SOURCE, statement) or ""
        for statement in node.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    )
    assert "new_part" not in body
    assert "launch" in body
