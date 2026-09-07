"""Empty sketches stop piling up: the next one is refused, not warned about.

Reported and not acted on until 2026-09-06, and reporting was not enough.
Measured twice on the seat:

* ladder prompt H4 run 1 -- eight empty sketches in the tree and a bare strip
  of a part;
* ladder prompt PRO1 run 2 -- `Frame profile`, `C-Frame outline` and `Frame
  outline`, each created and closed without a line drawn in any of them, while
  the twenty-round turn ran out.

An empty sketch is a perfectly successful `sketch_create`, so nothing failed
and nothing said stop. Creating a second one before drawing in the first is
never the way out of anything: whatever went wrong with the last sketch is
still wrong, and the tree fills with names `catia_list_features` then has to
report and the agent has to read past.

Two decisions here, both of which could have gone the other way:

* **The threshold is two, not one.** One empty sketch is the ordinary state
  between `sketch_create` and the first line, and a caller that creates a
  sketch, thinks again and creates another on a different plane is doing
  something reasonable. Three is a loop.
* **Refuse, do not delete.** The sketch the caller means to draw into on the
  very next call is empty at exactly this moment, so tidying them away here
  would throw out the one that was about to be used.

Offline: fake sketch collections, no CATIA.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.com.sketcher import SketcherMixin, _is_empty  # noqa: E402


class _Elements:
    def __init__(self, count: int) -> None:
        self.Count = count  # noqa: N815 - COM spelling


class _Sketch:
    def __init__(self, name: str, elements: int) -> None:
        self.Name = name
        self.GeometricElements = _Elements(elements)  # noqa: N815 - COM spelling


class _Part:
    """Only the two hooks `_refuse_to_pile_up_empty_sketches` reaches for."""

    def __init__(self, sketches: list[_Sketch]) -> None:
        self._sketches = sketches

    def _sketches_in_part(self):
        return self._sketches

    def _empty_sketch_count(self) -> int:
        return sum(1 for sketch in self._sketches if _is_empty(sketch))

    refuse = SketcherMixin._refuse_to_pile_up_empty_sketches


def part(*sketches: tuple[str, int]) -> _Part:
    return _Part([_Sketch(name, count) for name, count in sketches])


class TestWhenItRefuses:
    def test_two_empty_sketches_stop_a_third(self) -> None:
        with pytest.raises(CatiaOperationError, match="2 sketches with nothing drawn"):
            part(("Frame profile", 1), ("C-Frame outline", 1)).refuse()

    def test_the_pro1_tree_is_refused(self) -> None:
        """The three that were actually created."""
        with pytest.raises(CatiaOperationError) as raised:
            part(
                ("Frame profile", 1), ("C-Frame outline", 1), ("Frame outline", 1)
            ).refuse()
        assert "Frame profile" in str(raised.value)

    def test_one_empty_sketch_is_the_ordinary_state(self) -> None:
        """Between `sketch_create` and the first line, every part looks like
        this. Refusing here would make the tool unusable."""
        part(("Sketch.1", 1)).refuse()

    def test_no_sketches_at_all_is_fine(self) -> None:
        part().refuse()

    def test_sketches_with_geometry_never_count(self) -> None:
        part(("profile", 6), ("bore", 2), ("slot", 4)).refuse()

    def test_a_mixed_tree_counts_only_the_empty_ones(self) -> None:
        part(("profile", 6), ("Sketch.2", 1)).refuse()
        with pytest.raises(CatiaOperationError):
            part(("profile", 6), ("Sketch.2", 1), ("Sketch.3", 1)).refuse()


class TestWhatItSays:
    def test_it_names_the_sketches(self) -> None:
        """So the caller can draw into one rather than guess which."""
        with pytest.raises(CatiaOperationError) as raised:
            part(("alpha", 1), ("beta", 1)).refuse()
        message = str(raised.value)
        assert "alpha" in message and "beta" in message

    def test_it_says_nothing_was_changed(self) -> None:
        with pytest.raises(CatiaOperationError, match="Nothing was\\s+changed"):
            part(("a", 1), ("b", 1)).refuse()

    def test_it_names_both_ways_out(self) -> None:
        with pytest.raises(CatiaOperationError) as raised:
            part(("a", 1), ("b", 1)).refuse()
        message = str(raised.value)
        assert "catia_sketch_polyline" in message
        assert "catia_sketch_close" in message

    def test_it_does_not_offer_more_sketches_as_a_remedy(self) -> None:
        with pytest.raises(CatiaOperationError) as raised:
            part(("a", 1), ("b", 1)).refuse()
        assert "will not make the last one build" in str(raised.value)


class TestEmptiness:
    def test_a_sketch_with_only_its_axis_is_empty(self) -> None:
        assert _is_empty(_Sketch("s", 1)) is True

    def test_a_sketch_with_a_line_is_not(self) -> None:
        assert _is_empty(_Sketch("s", 2)) is False

    def test_an_unreadable_sketch_is_not_called_empty(self) -> None:
        """Guessing empty would delete-by-refusal a sketch nobody can see
        into."""

        class _Broken:
            @property
            def GeometricElements(self):  # noqa: N802 - COM spelling
                raise RuntimeError("no")

        assert _is_empty(_Broken()) is False


class TestItRunsBeforeAnythingIsCreated:
    def test_the_reuse_happens_before_anything_is_created(self) -> None:
        """Rewritten 2026-09-07, when the refusal became a reuse.

        The claim is unchanged and still worth pinning: whatever this does about
        the pile-up, it has to happen *before* `Sketches.Add`, or a fourth empty
        sketch lands in the tree and then gets complained about. What changed is
        the answer -- an empty sketch on the wanted plane is handed over rather
        than the call refused, because most of those empties are debris the
        profile tools create themselves and refusing cost a whole run.
        """
        import inspect

        body = inspect.getsource(SketcherMixin.sketch_create)
        assert body.index("_reuse_an_empty_sketch") < body.index("Sketches.Add")

    def test_the_reuse_returns_before_a_new_sketch_is_added(self) -> None:
        """It must actually return, not merely run first."""
        import inspect

        body = inspect.getsource(SketcherMixin.sketch_create)
        head = body[: body.index("Sketches.Add")]
        assert "return reused" in head

    def test_only_a_sketch_on_the_same_support_is_reused(self) -> None:
        """Handing over a sketch on another plane would silently draw the
        profile somewhere else -- far worse than an extra name in the tree."""
        import inspect

        body = inspect.getsource(SketcherMixin._reuse_an_empty_sketch)
        assert "AbsoluteAxis.Parent.Name" in body
        assert "continue" in body

    def test_a_part_with_fewer_than_two_empties_creates_a_fresh_one(self) -> None:
        """The ordinary path is untouched: one empty sketch between
        `sketch_create` and the first line is normal and reuses nothing."""
        import inspect

        body = inspect.getsource(SketcherMixin._reuse_an_empty_sketch)
        assert "if self._empty_sketch_count() < 2:" in body
        assert "return None" in body
