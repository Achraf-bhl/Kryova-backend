"""Starting the next thing is what closing a sketch means.

`SketcherMixin` holds a sketch open across calls so a multi-primitive profile
is expressible. Everything else then had to cope with one being open, and the
way it coped was `_require_closed`: refuse the call, and tell the agent to
send `catia_sketch_close` first.

That refusal cost more than the hazard, measured on ladder prompt H3 across
three runs on 2026-09-06:

* Run 3, the recorded one: sketch, rectangle, pad (CATIA accepted the pad with
  the sketch still in edition), fillet -- and then `catia_sketch_create` for the
  pocket profile was refused with "The sketch 'Sketch.1' is still open. Call
  catia_sketch_close before building a feature from it". Two of the twenty tool
  rounds went on obeying that, and the turn ran out three calls short of the
  pocket.
* An earlier run recovered worse: instead of the tool, the agent passed the
  string "Close Sketch" to `catia_run_command`. CATIA does not know that name,
  raised its modal unknown-command box, and held COM until a human pressed OK
  (`tests/test_catia_unavailable_states.py` is that incident).

A person never sees this refusal, because clicking Pad *is* leaving the
Sketcher. So the 3D operations end the edition themselves. Nothing about the
sketch changes -- `CloseEdition` ends editing, it does not touch geometry --
and the direction that matters is kept: `_open_sketch` still refuses to draw
into a sketch that is not open, because drawing into the wrong sketch is a
mistake that silently produces the wrong part.

Offline: no CATIA, no seat. A fake sketch records whether it was closed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge"
sys.path.insert(0, str(BRIDGE.parent))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.com.sketcher import SketcherMixin  # noqa: E402


class _Sketch:
    def __init__(self, name: str) -> None:
        self.Name = name  # noqa: N815 - COM spelling
        self.closed = 0
        self.opened = 0

    def CloseEdition(self) -> None:  # noqa: N802 - COM spelling
        self.closed += 1

    def OpenEdition(self) -> object:  # noqa: N802 - COM spelling
        self.opened += 1
        return object()


class _Sketches:
    def __init__(self, sketches: list[_Sketch]) -> None:
        self._items = sketches

    @property
    def Count(self) -> int:  # noqa: N802 - COM spelling
        return len(self._items)

    def Item(self, index: int) -> _Sketch:  # noqa: N802 - COM spelling
        return self._items[index - 1]


class _Body:
    def __init__(self, sketches: list[_Sketch]) -> None:
        self.Sketches = _Sketches(sketches)  # noqa: N815 - COM spelling


class _Part:
    def __init__(self) -> None:
        self.updates = 0

    def Update(self) -> None:  # noqa: N802 - COM spelling
        self.updates += 1


class _Holder(SketcherMixin):
    """Just enough of `CatiaCom` to exercise the edition state."""

    def __init__(
        self, open_sketch: _Sketch | None = None, others: list[_Sketch] | None = None
    ) -> None:
        self._sketch_edition = (open_sketch, object()) if open_sketch else None
        self.part = _Part()
        sketches = list(others or [])
        if open_sketch is not None and open_sketch not in sketches:
            sketches.insert(0, open_sketch)
        self.body = _Body(sketches)

    def _part(self) -> _Part:
        return self.part

    def _body(self) -> _Body:
        return self.body


class TestEndingTheEdition:
    def test_it_closes_the_open_sketch_and_says_which(self) -> None:
        sketch = _Sketch("Sketch.1")
        holder = _Holder(sketch)
        assert holder._end_sketch_edition() == "Sketch.1"
        assert sketch.closed == 1
        assert holder._sketch_edition is None
        assert holder.part.updates == 0, (
            "the implicit close must not Update: the operation that triggered it is "
            "about to build a feature and update once, and an extra rebuild on a "
            "large part is seconds of the user's time for nothing"
        )

    def test_it_is_a_no_op_when_nothing_is_open(self) -> None:
        """Every 3D operation calls this, so the ordinary case is no sketch."""
        assert _Holder()._end_sketch_edition() is None

    def test_calling_it_twice_closes_once(self) -> None:
        """CloseEdition on an ended edition is a COM error, and the state is
        cleared before anything can call it again."""
        sketch = _Sketch("Sketch.1")
        holder = _Holder(sketch)
        holder._end_sketch_edition()
        holder._end_sketch_edition()
        assert sketch.closed == 1

    def test_it_raises_nothing_it_could_be_asked_to_refuse(self) -> None:
        """The whole point: this replaced a refusal, so it must not be one."""
        holder = _Holder(_Sketch("Esquisse.1"))
        holder._end_sketch_edition()  # would raise if the old rule survived


class TestDrawingStillRefuses:
    """The other direction is kept, and it is the one worth keeping.

    Building a solid from a sketch that is open is harmless -- CATIA does it,
    and the edition ends first now anyway. Drawing into a sketch that is *not*
    open is not harmless: the line goes somewhere the agent did not intend, or
    nowhere, and the part is wrong with every call reporting success.
    """

    def test_drawing_with_no_sketch_open_is_refused_by_name(self) -> None:
        with pytest.raises(CatiaOperationError) as raised:
            _Holder()._open_sketch()
        assert "catia_sketch_create" in str(raised.value)

    def test_naming_a_sketch_that_is_not_in_the_part_is_refused(self) -> None:
        """The case that stays an error, and the reason the check exists:
        drawing into a sketch that does not exist would otherwise land the
        geometry somewhere the caller did not mean, with every call reporting
        success."""
        with pytest.raises(CatiaOperationError) as raised:
            _Holder(_Sketch("Sketch.1"))._open_sketch("Sketch.9")
        message = str(raised.value)
        assert "Sketch.9" in message
        assert "Sketch.1" in message, "the refusal must say what the part does have"


class TestSwitchingSketches:
    """Naming another sketch is a request to edit it.

    Measured on ladder prompt H4, 2026-09-06, twice in one run: the agent
    created Sketch.3, then called catia_sketch_polygon with sketch='Sketch.2'
    and was told "'Sketch.2' is not the open sketch ('Sketch.3' is). Close
    that one with catia_sketch_close before editing another". True, correct,
    and a round of twenty each time -- and the recovery it suggests is a call
    the agent then has to get right as well.

    A person double-clicks the sketch in the tree, and CATIA ends the edition
    they were in. That is what this does.
    """

    def test_it_opens_the_named_sketch(self) -> None:
        wanted = _Sketch("Sketch.2")
        holder = _Holder(_Sketch("Sketch.3"), others=[wanted])
        sketch, factory = holder._open_sketch("Sketch.2")
        assert sketch is wanted
        assert factory is not None
        assert wanted.opened == 1

    def test_it_ends_the_edition_it_was_in(self) -> None:
        """Two open editions is a state CATIA does not have."""
        was_open = _Sketch("Sketch.3")
        holder = _Holder(was_open, others=[_Sketch("Sketch.2")])
        holder._open_sketch("Sketch.2")
        assert was_open.closed == 1
        assert holder._sketch_edition[0].Name == "Sketch.2"

    def test_naming_the_one_already_open_changes_nothing(self) -> None:
        """It must not close and reopen the sketch being drawn into: that is a
        rebuild per primitive on a part where every call is a network hop."""
        open_sketch = _Sketch("Sketch.1")
        holder = _Holder(open_sketch)
        holder._open_sketch("Sketch.1")
        assert open_sketch.closed == 0
        assert open_sketch.opened == 0

    def test_no_name_still_means_the_open_one(self) -> None:
        open_sketch = _Sketch("Sketch.1")
        sketch, _ = _Holder(open_sketch, others=[_Sketch("Sketch.2")])._open_sketch()
        assert sketch is open_sketch

    def test_it_can_open_one_when_nothing_is_open(self) -> None:
        """After a pad, no sketch is in edition. Naming one is still a request
        to draw into it, and refusing here would send the agent to
        catia_sketch_create, which would refuse the duplicate name."""
        wanted = _Sketch("Sketch.1")
        holder = _Holder(others=[wanted])
        sketch, _ = holder._open_sketch("Sketch.1")
        assert sketch is wanted
        assert wanted.opened == 1


class TestNothingRefusesAnOpenSketchAnyMore:
    """The rule, structurally, so it cannot come back one operation at a time.

    Twenty-six call sites used to call `_require_closed`. A single one restored
    would be a tool that refuses mid-build for a reason the agent cannot see
    from its own arguments -- which is exactly how H3 lost its rounds.
    """

    def _sources(self) -> dict[Path, str]:
        paths = [BRIDGE / "catia_com.py", *sorted((BRIDGE / "com").glob("*.py"))]
        return {path: path.read_text(encoding="utf-8") for path in paths}

    def test_no_call_site_refuses_instead_of_closing(self) -> None:
        offenders = [
            f"{path.name}:{number}"
            for path, source in self._sources().items()
            for number, line in enumerate(source.splitlines(), start=1)
            if "_require_closed()" in line
        ]
        assert not offenders, (
            f"These refuse a call because a sketch is open: {offenders}. Call "
            "self._end_sketch_edition() instead -- leaving the Sketcher is what "
            "starting the next operation means."
        )

    def test_the_refusal_text_is_gone_from_the_bridge(self) -> None:
        """Not just the helper: the sentence itself, in case someone inlines it."""
        for path, source in self._sources().items():
            if path.name == "sketcher.py":
                continue  # its docstring quotes the old message on purpose
            assert "is still open. Call catia_sketch_close" not in source, path.name

    def test_every_sketch_consuming_operation_ends_the_edition(self) -> None:
        """The pad/pocket/shaft family reaches its profile through
        `_find_sketch`, which is where the edition ends. This asserts that is
        still true rather than trusting each of them to remember."""
        source = (BRIDGE / "catia_com.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        finder = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_find_sketch"
        )
        body = ast.get_source_segment(source, finder) or ""
        assert "_end_sketch_edition()" in body, (
            "_find_sketch no longer ends the open edition, so catia_pad on the "
            "sketch just drawn is back to depending on the agent having closed it"
        )


class TestCreatingTheNextSketch:
    def test_it_reports_the_one_it_closed(self) -> None:
        """Silently closing would be the mirror of the bug: the agent's model
        of the session must match the seat's, or its next `catia_sketch_close`
        is a call it did not need to make. `closed_previous` is one word in the
        result and saves a round.
        """
        source = (BRIDGE / "com" / "sketcher.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        create = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "sketch_create"
        )
        body = ast.get_source_segment(source, create) or ""
        assert "_end_sketch_edition()" in body
        assert "closed_previous" in body

    def test_closing_by_hand_still_works(self) -> None:
        """`catia_sketch_close` is not deprecated by this. An agent that closes
        its sketch explicitly is doing the right thing and must not now be told
        there is nothing to close."""
        sketch = _Sketch("Sketch.1")
        holder = _Holder(sketch)
        result: dict[str, Any] = SketcherMixin.sketch_close(holder)  # type: ignore[arg-type]
        assert result == {"sketch": "Sketch.1", "open": False}
        assert sketch.closed == 1
        assert holder.part.updates == 1, (
            "the explicit close still updates the part -- it is the one place the "
            "agent asked for the sketch to take effect and expects to be told it did"
        )

    def test_closing_nothing_is_still_refused(self) -> None:
        """This refusal stays: it means the agent thinks it is drawing and is
        not, which is worth interrupting for."""
        with pytest.raises(CatiaOperationError):
            SketcherMixin.sketch_close(_Holder())  # type: ignore[arg-type]


class TestClosingWhatIsAlreadyClosed:
    """Asking for a state that already holds is a success, not an error.

    Measured on ladder prompt H4, 2026-09-06: three of twenty rounds went on

        catia_sketch_close: 'Sketch.2' is not the open sketch ('BracketProfile' is)

    The agent had lost track of which sketch it had open -- which happens --
    and closing them one at a time is exactly how a careful caller recovers.
    Every one of those calls asked for something that was already true.

    A name the part does not have is still refused, because reporting it
    closed would be a false statement about the part.
    """

    def test_closing_a_sketch_that_is_not_open_succeeds(self) -> None:
        holder = _Holder(_Sketch("BracketProfile"), others=[_Sketch("Sketch.2")])
        result = SketcherMixin.sketch_close(holder, sketch="Sketch.2")  # type: ignore[arg-type]
        assert result["open"] is False
        assert result["already_closed"] is True

    def test_it_says_which_sketch_is_actually_open(self) -> None:
        """Otherwise the caller has learnt nothing and closes the next one at
        random."""
        holder = _Holder(_Sketch("BracketProfile"), others=[_Sketch("Sketch.2")])
        result = SketcherMixin.sketch_close(holder, sketch="Sketch.2")  # type: ignore[arg-type]
        assert "BracketProfile" in result["note"]

    def test_the_open_sketch_is_left_open(self) -> None:
        """Closing Sketch.2 must not close the one being drawn into. That
        would be a different operation than the one requested, and the caller
        would find its next drawing call refused."""
        open_sketch = _Sketch("BracketProfile")
        holder = _Holder(open_sketch, others=[_Sketch("Sketch.2")])
        SketcherMixin.sketch_close(holder, sketch="Sketch.2")  # type: ignore[arg-type]
        assert open_sketch.closed == 0
        assert holder._sketch_edition is not None

    def test_closing_one_when_nothing_is_open_succeeds(self) -> None:
        """After a pad, every sketch is closed. Saying so is the truth."""
        holder = _Holder(others=[_Sketch("Sketch.1")])
        result = SketcherMixin.sketch_close(holder, sketch="Sketch.1")  # type: ignore[arg-type]
        assert result["already_closed"] is True

    def test_a_name_the_part_does_not_have_is_still_refused(self) -> None:
        holder = _Holder(_Sketch("Sketch.1"))
        with pytest.raises(CatiaOperationError) as raised:
            SketcherMixin.sketch_close(holder, sketch="Sketch.9")  # type: ignore[arg-type]
        assert "Sketch.9" in str(raised.value)
        assert "Sketch.1" in str(raised.value)

    def test_closing_the_open_one_by_name_still_closes_it(self) -> None:
        open_sketch = _Sketch("Sketch.1")
        holder = _Holder(open_sketch)
        result = SketcherMixin.sketch_close(holder, sketch="Sketch.1")  # type: ignore[arg-type]
        assert open_sketch.closed == 1
        assert result.get("already_closed") is None
