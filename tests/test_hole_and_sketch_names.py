"""Two more of H4's twenty rounds, spent on things that could not work.

**The hole had no depth.** `catia_hole_at` set `hole.Depth.Value`, and V5-R33's
Hole object has no `Depth`. pywin32 reports that as `AddNewHoleFromPoint.Depth`
-- which reads like a CATIA failure and is an `AttributeError` -- so every
blind hole ever drilled through this tool failed, *after* creating the hole,
with a message blaming the point for being off the face. Measured on the seat
2026-09-06; the depth is `hole.BottomLimit.Dimension.Value`, and with that one
change a Ø8 hole 12 deep in a 100x60x30 block leaves 179,358.12 mm3 against
179,358.11 arithmetic (the drill point's 118-degree cone included).

`HeadDiameter` is the same class of surprise from the other side: it exists
only on the hole types that have a head, and raises `La methode HeadDiameter a
echoue` on a simple hole. A caller asking for one on a simple hole is making a
mistake worth naming, not a COM error worth forwarding.

**Two sketches answered to one name.** CATIA holds any number of sketches
called `Sketch.1`, and everything here that resolves one by name takes the
first it finds. H4's tree ended as

    Sketch.1 (4 elements), Sketch.2 (0), Sketch.1 (0), Sketch.3 (0),
    Sketch.wall (0), Sketch.wall (0)

-- at which point no tool, and no person, could say which `Sketch.1` a pad
would extrude. Now refused, naming the sketch that already exists.

The eight empty sketches are the other half of that run and are *not* refused:
an empty sketch is what `catia_sketch_create` legitimately produces, and the
agent's next call is normally the one that draws into it. What was missing was
any sign that this had happened eight times, so the result says so once it is
clearly a loop rather than a step.

Offline: fakes with the COM surface, no CATIA.
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
from catia_bridge.com.part_design import (  # noqa: E402
    refuse_a_head_this_hole_has_not,
)
from catia_bridge.com.sketcher import SketcherMixin  # noqa: E402

PART_DESIGN = (BRIDGE / "com" / "part_design.py").read_text(encoding="utf-8")


def hole_at_body() -> str:
    tree = ast.parse(PART_DESIGN)
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "hole_at"
    )
    return "\n".join(
        ast.get_source_segment(PART_DESIGN, statement) or ""
        for statement in node.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    )


class TestTheHolesDepth:
    def test_the_depth_is_set_through_the_bottom_limit(self) -> None:
        body = hole_at_body()
        assert "BottomLimit.Dimension.Value = float(depth_mm)" in body

    def test_the_property_that_does_not_exist_is_not_assigned(self) -> None:
        """`hole.Depth = ...` is the defect. The comment beside the fix names
        it, so the check is on the statements and not on the explanation."""
        code = "\n".join(
            line for line in hole_at_body().splitlines() if not line.strip().startswith("#")
        )
        assert "hole.Depth" not in code, (
            "catia_hole_at is assigning hole.Depth again -- V5-R33 has no such "
            "property, and the AttributeError arrives dressed as a CATIA error "
            "about the point being off the face"
        )

    def test_a_through_hole_still_takes_no_depth(self) -> None:
        """`through_all` sets the limit mode instead, and must not also try to
        write a dimension that was never given."""
        body = hole_at_body()
        limit = body.index("BottomLimit.LimitMode = 1")
        dimension = body.index("BottomLimit.Dimension.Value")
        assert limit < dimension
        assert "else:" in body[limit:dimension]


class TestTheHeadOfAHole:
    def test_the_headed_kinds_are_the_ones_catia_has(self) -> None:
        from catia_bridge.com.part_design import _HEADED_HOLE_KINDS, _HOLE_TYPES

        assert _HEADED_HOLE_KINDS <= set(_HOLE_TYPES)
        assert _HEADED_HOLE_KINDS == {"counterbored", "countersunk", "counterdrilled"}

    @pytest.mark.parametrize("kind", ["simple", "tapered"])
    def test_a_head_on_a_hole_without_one_is_refused(self, kind: str) -> None:
        with pytest.raises(CatiaOperationError) as raised:
            refuse_a_head_this_hole_has_not(
                kind, head_diameter_mm=12.0, head_depth_mm=None, head_angle_deg=None
            )
        assert "head_diameter_mm" in str(raised.value)
        assert kind in str(raised.value)

    @pytest.mark.parametrize("kind", ["counterbored", "countersunk", "counterdrilled"])
    def test_a_head_on_a_hole_that_has_one_passes(self, kind: str) -> None:
        refuse_a_head_this_hole_has_not(
            kind, head_diameter_mm=12.0, head_depth_mm=4.0, head_angle_deg=90.0
        )

    def test_a_simple_hole_with_no_head_values_passes(self) -> None:
        """The ordinary call, which must not be refused by a guard about
        something it never asked for."""
        refuse_a_head_this_hole_has_not(
            "simple", head_diameter_mm=None, head_depth_mm=None, head_angle_deg=None
        )

    def test_every_head_value_is_named_in_the_refusal(self) -> None:
        """All three, not just the first: an agent that fixes one and resends
        gets refused again on the next, a round each."""
        with pytest.raises(CatiaOperationError) as raised:
            refuse_a_head_this_hole_has_not(
                "simple", head_diameter_mm=12.0, head_depth_mm=4.0, head_angle_deg=90.0
            )
        message = str(raised.value)
        for name in ("head_diameter_mm", "head_depth_mm", "head_angle_deg"):
            assert name in message

    def test_the_refusal_names_the_kinds_that_would_work(self) -> None:
        with pytest.raises(CatiaOperationError) as raised:
            refuse_a_head_this_hole_has_not(
                "simple", head_diameter_mm=12.0, head_depth_mm=None, head_angle_deg=None
            )
        assert "counterbored" in str(raised.value)
        assert "countersunk" in str(raised.value)

    def test_hole_at_actually_calls_it(self) -> None:
        """A guard nothing calls is a guard nobody has."""
        body = hole_at_body()
        assert "refuse_a_head_this_hole_has_not(" in body
        assert body.index("refuse_a_head_this_hole_has_not(") < body.index("hole.HeadDiameter")


class _Sketch:
    def __init__(self, name: str, elements: int = 1) -> None:
        self.Name = name  # noqa: N815 - COM spelling
        self.closed = 0
        self.GeometricElements = type("_G", (), {"Count": elements})()  # noqa: N815

    def CloseEdition(self) -> None:  # noqa: N802 - COM spelling
        self.closed += 1


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


class _Holder(SketcherMixin):
    def __init__(self, sketches: list[_Sketch] | None = None) -> None:
        self._sketch_edition = None
        self.body = _Body(sketches or [])

    def _body(self) -> Any:
        return self.body


class TestOneNameOneSketch:
    def test_a_name_already_in_the_part_is_refused(self) -> None:
        holder = _Holder([_Sketch("Sketch.1", elements=5)])
        with pytest.raises(CatiaOperationError) as raised:
            holder._refuse_a_duplicate_name("Sketch.1")
        assert "Sketch.1" in str(raised.value)

    def test_the_refusal_says_how_to_use_the_one_that_exists(self) -> None:
        """The agent's intent was to draw a profile. Telling it only that the
        name is taken sends it round the loop again with `Sketch.wall.2`."""
        holder = _Holder([_Sketch("Sketch.wall")])
        with pytest.raises(CatiaOperationError) as raised:
            holder._refuse_a_duplicate_name("Sketch.wall")
        assert "catia_sketch_rectangle" in str(raised.value)

    def test_a_free_name_passes(self) -> None:
        _Holder([_Sketch("Sketch.1")])._refuse_a_duplicate_name("Sketch.2")

    def test_an_unnamed_sketch_is_never_refused(self) -> None:
        """`name` is optional, and CATIA numbers an unnamed sketch itself."""
        body = ast.parse((BRIDGE / "com" / "sketcher.py").read_text(encoding="utf-8"))
        create = next(
            n for n in ast.walk(body) if isinstance(n, ast.FunctionDef) and n.name == "sketch_create"
        )
        source = ast.get_source_segment(
            (BRIDGE / "com" / "sketcher.py").read_text(encoding="utf-8"), create
        )
        assert "if name:" in (source or "")


class TestEmptySketchesAreReportedNotDeleted:
    def test_a_part_full_of_empty_sketches_is_counted(self) -> None:
        holder = _Holder([_Sketch(f"Sketch.{n}", elements=1) for n in range(1, 6)])
        assert holder._empty_sketch_count() == 5

    def test_a_sketch_with_geometry_is_not_empty(self) -> None:
        """CATIA counts the sketch's own axis as an element, so a drawn
        rectangle reads as five and an empty sketch as one. Counting anything
        with `Count == 0` as the empty case would find none of them."""
        holder = _Holder([_Sketch("Sketch.1", elements=5), _Sketch("Sketch.2", elements=1)])
        assert holder._empty_sketch_count() == 1

    def test_an_unreadable_sketch_is_not_counted_as_empty(self) -> None:
        class _Broken:
            Name = "Sketch.1"  # noqa: N815 - COM spelling

            @property
            def GeometricElements(self) -> Any:  # noqa: N802 - COM spelling
                raise RuntimeError("La methode GeometricElements a echoue")

        assert _Holder([_Broken()])._empty_sketch_count() == 0  # type: ignore[list-item]

    def test_nothing_is_deleted(self) -> None:
        """Deleting an empty sketch is the wrong fix: the sketch the caller is
        about to draw into is empty at exactly this moment."""
        source = (BRIDGE / "com" / "sketcher.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_empty_sketch_count"
        )
        body = ast.get_source_segment(source, node) or ""
        assert "Delete" not in body and "Remove" not in body
