"""A construction plane must not stop the part taking solid features.

The defect, measured on the seat on 2026-09-06 during ladder prompt H3 and
reproduced from bare COM three times:

    AddNewPad (100x60 block)                  -> Extrusion.1, 180000 mm3
    HybridBodies.Add()                        -> in work: Kryova Construction
    AddNewPocket (40x20x10 on the top face)   -> La methode AddNewPocket a echoue
    part.InWorkObject = body; AddNewPocket    -> Poche.1, 172000 mm3

`ShapeFactory` inserts a new solid feature after the part's **in-work object**.
`HybridBodies.Add()` makes the new geometrical set the in-work object, and a
geometrical set cannot hold a pad -- so creation fails, before any profile is
even looked at. The bridge builds a geometrical set the first time anything
needs construction geometry, which includes `catia_sketch_create` with
`support="top"` or any other named face. So one sketch on a face left the part
unable to take another pad, pocket or hole for the rest of the session, and the
message blamed the profile:

    "CATIA would not start a pocket from Sketch.2 ... The profile has to be one
     closed loop that CATIA can sweep"

which is a diagnosis of something that was not wrong. The agent believed it,
drew the rectangle again, then drew a construction circle to "clear" the
sketch, and the turn ran out.

Two guards, because they answer different questions. `geometrical_set` putting
the in-work object back is the fix at the cause: making construction geometry
says nothing about where the next pad belongs. `_body` asserting it is the
guarantee, and it covers what the first one cannot -- a person on the seat
clicking Define In Work Object on a geometrical set between two messages.

Offline: fakes with the same COM surface, no CATIA.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge"
sys.path.insert(0, str(BRIDGE.parent))

from catia_bridge.catia_com import CatiaCom  # noqa: E402
from catia_bridge.com._context import geometrical_set  # noqa: E402


class _Named:
    def __init__(self, name: str) -> None:
        self.Name = name  # noqa: N815 - COM spelling


class _HybridBodies:
    """`HybridBodies`, including the side effect that caused all of this."""

    def __init__(self, part: _Part, existing: list[str] | None = None) -> None:
        self._part = part
        self._items = [_Named(name) for name in (existing or [])]

    @property
    def Count(self) -> int:  # noqa: N802 - COM spelling
        return len(self._items)

    def Item(self, index: int) -> _Named:  # noqa: N802 - COM spelling
        return self._items[index - 1]

    def Add(self) -> _Named:  # noqa: N802 - COM spelling
        created = _Named(f"Geometrical Set.{len(self._items) + 1}")
        self._items.append(created)
        self._part.InWorkObject = created  # what CATIA does, and the whole bug
        return created


class _Part:
    def __init__(self, existing_sets: list[str] | None = None) -> None:
        self.MainBody = _Named("Corps principal")  # noqa: N815 - COM spelling
        self.InWorkObject: Any = self.MainBody  # noqa: N815 - COM spelling
        self.HybridBodies = _HybridBodies(self, existing_sets)  # noqa: N815


class _Com(CatiaCom):
    """`CatiaCom._body` against a fake part, with no COM and no __init__."""

    def __init__(self, part: _Part) -> None:  # noqa: D107 - see class docstring
        self.part = part

    def _part(self) -> _Part:  # type: ignore[override]
        return self.part


class TestCreatingASetLeavesTheInsertionPointAlone:
    def test_the_body_is_still_in_work_afterwards(self) -> None:
        part = _Part()
        geometrical_set(part)
        assert part.InWorkObject is part.MainBody, (
            "the geometrical set is in work, so the next AddNewPad fails at "
            "creation with 'La methode AddNewPad a echoue'"
        )

    def test_the_set_is_still_created_and_named(self) -> None:
        """The restore must not cost the thing the function is for."""
        part = _Part()
        created = geometrical_set(part)
        assert created.Name == "Kryova Construction"
        assert part.HybridBodies.Count == 1

    def test_an_existing_set_is_reused_and_nothing_moves(self) -> None:
        part = _Part(existing_sets=["Kryova Construction"])
        found = geometrical_set(part)
        assert found.Name == "Kryova Construction"
        assert part.HybridBodies.Count == 1
        assert part.InWorkObject is part.MainBody

    def test_a_named_set_is_created_the_same_way(self) -> None:
        part = _Part()
        assert geometrical_set(part, "Wireframe").Name == "Wireframe"
        assert part.InWorkObject is part.MainBody

    def test_it_restores_whatever_was_in_work_not_the_body(self) -> None:
        """A second body is a real case -- `catia_new_body` exists -- and the
        caller was building into it. Forcing MainBody here would move their
        work to a different body without telling them."""
        part = _Part()
        other = _Named("Body.2")
        part.InWorkObject = other
        geometrical_set(part)
        assert part.InWorkObject is other


class TestTheBodyIsWhereFeaturesLand:
    def test_a_set_left_in_work_by_anything_else_is_corrected(self) -> None:
        """The case `geometrical_set` cannot cover: a person on the seat
        clicking Define In Work Object, or a macro, between two messages."""
        part = _Part()
        part.InWorkObject = _Named("Kryova Construction")
        assert _Com(part)._body() is part.MainBody
        assert part.InWorkObject is part.MainBody

    def test_the_ordinary_case_writes_nothing(self) -> None:
        """`_body` is called by read operations too, so the common path must
        not touch the document at all."""
        part = _Part()

        writes: list[Any] = []

        class _Watched(_Part):
            def __setattr__(self, name: str, value: Any) -> None:
                if name == "InWorkObject" and getattr(self, "_ready", False):
                    writes.append(value)
                object.__setattr__(self, name, value)

        watched = _Watched()
        watched._ready = True
        assert _Com(watched)._body() is watched.MainBody
        assert writes == [], "an in-work write on a part that was already correct"
        assert part.InWorkObject is part.MainBody

    def test_a_part_with_no_in_work_object_is_not_an_error(self) -> None:
        """Some documents raise on the property. A read must not fail because
        of a cursor position."""

        class _NoInWork:
            def __init__(self) -> None:
                self.MainBody = _Named("Corps principal")  # noqa: N815

            @property
            def InWorkObject(self) -> Any:  # noqa: N802 - COM spelling
                raise RuntimeError("La methode InWorkObject a echoue")

        part = _NoInWork()
        assert _Com(part)._body() is part.MainBody  # type: ignore[arg-type]


class TestTheRuleIsWrittenDown:
    def test_both_places_explain_the_measured_failure(self) -> None:
        """A bare `part.InWorkObject = body` reads like superstition and gets
        removed by the next person tidying up. The measurement is why it stays."""
        context = (BRIDGE / "com" / "_context.py").read_text(encoding="utf-8")
        com = (BRIDGE / "catia_com.py").read_text(encoding="utf-8")
        assert "InWorkObject" in context and "in-work object" in context
        assert "in-work object" in com
