"""`catia_select` must find what `catia_list_features` just printed.

Measured on ladder prompt H4, 2026-09-06. The agent asked for the features,
was told `Sketch.1` was there, passed that exact string to `catia_select`, and
got back:

    Not in this part: Sketch.1. Call catia_list_features to see what is there;
    names are case-sensitive and end in a number.

It had. The name was right. A system that prints a name and then denies it
leaves the caller no way to be more correct, and the advice sends it back to
the tool that had already answered -- so the loop is closed and the round
budget drains.

Two causes, both here:

* **A sketch lives under the body, not the part.** `_find_named` looked in
  `part.Bodies`, `part.Sketches` (which does not exist) and
  `part.HybridBodies`, then fell back to `FindObjectByName`, which returned
  nothing for a sketch either. `body.Sketches` was never consulted, though
  `_find_sketch` two hundred lines away has always used it.
* **`Collection.Item(name)` is not reliable.** Measured on V5-R33: `Item`
  with a string raises `La methode Item a echoue` on `Sketches` and on
  `Bodies` even when a member of that name is in the collection. It takes an
  index dependably and a name only sometimes, so every name lookup walks the
  indices.

Offline: fakes with CATIA's collection surface, including its refusal to look
things up by name.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge"
sys.path.insert(0, str(BRIDGE.parent))

from catia_bridge.catia_com import _by_name, _find_named  # noqa: E402


class _Named:
    def __init__(self, name: str) -> None:
        self.Name = name  # noqa: N815 - COM spelling


class _Collection:
    """A CATIA collection: indexable, and unreliable by name.

    `Item` refuses a string the way the seat does, so a lookup that has gone
    back to `Item(name)` fails here for the same reason it fails there.
    """

    def __init__(self, names: list[str]) -> None:
        self._items = [_Named(name) for name in names]

    @property
    def Count(self) -> int:  # noqa: N802 - COM spelling
        return len(self._items)

    def Item(self, index: Any) -> _Named:  # noqa: N802 - COM spelling
        if not isinstance(index, int):
            raise RuntimeError("La methode Item a echoue")
        return self._items[index - 1]


class _Body:
    def __init__(self, sketches: list[str], shapes: list[str]) -> None:
        self.Name = "Corps principal"  # noqa: N815 - COM spelling
        self.Sketches = _Collection(sketches)  # noqa: N815 - COM spelling
        self.Shapes = _Collection(shapes)  # noqa: N815 - COM spelling


class _Part:
    """A part shaped like the seat's: no `Sketches`, and a body that has them."""

    def __init__(
        self,
        sketches: list[str] | None = None,
        shapes: list[str] | None = None,
        sets: list[str] | None = None,
        findable: list[str] | None = None,
    ) -> None:
        self.MainBody = _Body(sketches or [], shapes or [])  # noqa: N815
        self.Bodies = _Collection(["Corps principal"])  # noqa: N815
        self.HybridBodies = _Collection(sets or [])  # noqa: N815
        self._findable = {name: _Named(name) for name in (findable or [])}

    def FindObjectByName(self, name: str) -> Any:  # noqa: N802 - COM spelling
        found = self._findable.get(name)
        if found is None:
            raise RuntimeError("La methode FindObjectByName a echoue")
        return found


class TestFindingBySettingName:
    def test_a_sketch_under_the_body_is_found(self) -> None:
        """The H4 failure, exactly."""
        part = _Part(sketches=["Sketch.1", "Sketch.2"])
        found = _find_named(part, "Sketch.1")
        assert found is not None
        assert found.Name == "Sketch.1"

    def test_a_solid_feature_under_the_body_is_found(self) -> None:
        """`Extrusion.1` worked before this change and must keep working."""
        part = _Part(shapes=["Extrusion.1", "Conge arete.1"])
        assert _find_named(part, "Conge arete.1").Name == "Conge arete.1"

    def test_the_body_itself_is_found(self) -> None:
        assert _find_named(_Part(), "Corps principal").Name == "Corps principal"

    def test_a_geometrical_set_is_found(self) -> None:
        part = _Part(sets=["Kryova Construction"])
        assert _find_named(part, "Kryova Construction").Name == "Kryova Construction"

    def test_anything_else_still_falls_through_to_catia(self) -> None:
        """A construction plane, a parameter, an axis system -- things no
        collection here enumerates. `FindObjectByName` is the catch-all and
        stays the last resort, not the first."""
        part = _Part(findable=["Plane.3"])
        assert _find_named(part, "Plane.3").Name == "Plane.3"

    def test_a_name_that_is_not_there_returns_none(self) -> None:
        """The refusal has to stay possible: an invented name must not resolve
        to whatever happens to be first in a collection."""
        assert _find_named(_Part(sketches=["Sketch.1"]), "Sketch.9") is None

    def test_the_search_survives_a_part_with_no_body(self) -> None:
        """A drawing or a product reaching this must not raise."""

        class _NoBody:
            Bodies = _Collection([])  # noqa: N815 - COM spelling
            HybridBodies = _Collection([])  # noqa: N815 - COM spelling

            @property
            def MainBody(self) -> Any:  # noqa: N802 - COM spelling
                raise RuntimeError("La methode MainBody a echoue")

            def FindObjectByName(self, name: str) -> Any:  # noqa: N802 - COM spelling
                return _Named(name)

        assert _find_named(_NoBody(), "Sheet.1").Name == "Sheet.1"  # type: ignore[arg-type]


class TestLookingUpByName:
    def test_it_does_not_use_item_with_a_string(self) -> None:
        """The fake refuses `Item("Sketch.1")` the way the seat does, so a
        lookup that went back to it fails this test rather than passing on a
        collection that happens to be forgiving."""
        assert _by_name(_Collection(["Sketch.1", "Sketch.2"]), "Sketch.2").Name == "Sketch.2"

    def test_a_missing_name_is_none_not_an_error(self) -> None:
        assert _by_name(_Collection(["Sketch.1"]), "Sketch.4") is None

    def test_an_empty_collection_is_none(self) -> None:
        assert _by_name(_Collection([]), "Sketch.1") is None

    def test_one_unreadable_member_does_not_hide_the_rest(self) -> None:
        """A feature in error can refuse `.Name`. The one after it is still
        the one the caller asked for."""

        class _Broken(_Collection):
            def Item(self, index: Any) -> _Named:  # noqa: N802 - COM spelling
                if index == 1:
                    raise RuntimeError("La methode Name a echoue")
                return super().Item(index)

        assert _by_name(_Broken(["bad", "Sketch.2"]), "Sketch.2").Name == "Sketch.2"

    def test_something_that_is_not_a_collection_is_none(self) -> None:
        assert _by_name(object(), "Sketch.1") is None
