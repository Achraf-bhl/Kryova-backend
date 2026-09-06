"""A rectangle's width and height are named parameters, so they can be driven.

Measured on the seat, 2026-09-06, ladder prompt S1 -- "make a steel
counterweight 200 mm long and get it to 2.4 kg by adjusting only its width and
height, keeping them equal". The agent built a 62.88 kg block and could not
move it, and the reason was not the model. `catia_list_parameters` on a
40 x 40 x 200 block returned:

    Extrusion.1\\Première limite\\Longueur      200 mm
    Extrusion.1\\Sketch.1\\Contact.1\\Activité    1
    ... nine more booleans, and nothing else

The pad's length is a real parameter. The rectangle's width and height did not
exist as anything at all -- four free lines carry no dimensions -- so there was
nothing for `catia_set_parameter` to move and no way to converge on a mass
target by changing the section. The agent invented a parameter called
`WidthHeight`, was correctly refused, and had nowhere to go.

Two length constraints fix it. Verified on the real French V5-R33 seat the same
evening, through the chat endpoint, after the change:

    Part1\\Corps principal\\Extrusion.1\\Sketch.1\\width\\Longueur    40.0  mm
    Part1\\Corps principal\\Extrusion.1\\Sketch.1\\height\\Longueur   40.0  mm

Three decisions, each of which could have gone wrong quietly:

* **Two constraints, not four.** The rectangle stays under-constrained in
  position and rotation -- exactly as free as it was -- so nothing that already
  builds can become over-constrained. Ladder prompt H4 passes through this
  path and had to keep passing.
* **Named in English, on a French seat.** `width` and `height` are ours, and
  the automation API is not localised, so they read the same on every install.
  What CATIA wraps around them (`\\Longueur`) is its own and is translated,
  which is why the agent is told to find the name with catia_list_parameters
  rather than to construct it.
* **Best-effort, never fatal.** A release that will not take the constraint
  must not turn a working rectangle into a failed call. The failure is
  reported in `dimensions_named` and the geometry is exactly what it was.

Offline: source and signatures, no CATIA.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.com import sketcher  # noqa: E402

SOURCE = Path(sketcher.__file__).read_text(encoding="utf-8")


def _function(name: str) -> str:
    tree = ast.parse(SOURCE)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    return ast.get_source_segment(SOURCE, node) or ""


class TestTheConstraintsAreAdded:
    def test_the_helper_exists_and_is_called(self) -> None:
        """A helper nothing calls names nothing."""
        assert "_name_the_sides" in SOURCE
        assert "_name_the_sides(" in _function("sketch_rectangle")

    def test_the_default_names_are_width_and_height(self) -> None:
        body = _function("sketch_rectangle")
        assert '["width", "height"]' in body

    def test_it_uses_the_length_constraint(self) -> None:
        from catia_bridge.com.sketcher import _LENGTH_CONSTRAINT

        # catCstTypeLength. Published in CATIA's own enumeration, and unlike a
        # command label it is not localised.
        assert _LENGTH_CONSTRAINT == 5
        assert "_LENGTH_CONSTRAINT" in _function("_name_the_sides")

    def test_only_two_sides_are_constrained(self) -> None:
        """Four would over-constrain a rectangle whose corners are already
        coincident, and a red sketch will not pad."""
        body = _function("_name_the_sides")
        assert "lines[:2]" in body

    def test_the_constraint_is_given_a_value_and_a_name(self) -> None:
        body = _function("_name_the_sides")
        assert "Dimension.Value" in body
        assert "constraint.Name = name" in body


class TestItCannotBreakADrawingThatWorked:
    def test_the_naming_never_raises(self) -> None:
        """H4 passes through this path. A release that will not take the
        constraint must not turn a working rectangle into a failed call."""
        body = _function("_name_the_sides")
        assert body.count("except Exception") >= 2
        assert "return report" in body

    def test_a_failure_is_reported_rather_than_swallowed(self) -> None:
        """Silently not naming them is how S1 fails again with nobody knowing
        why."""
        body = _function("_name_the_sides")
        assert '"skipped"' in body

    def test_construction_geometry_is_not_dimensioned(self) -> None:
        """A construction rectangle is a guide, not a profile, and giving it
        driving dimensions would put parameters in the tree for a shape that
        is never built."""
        assert "if not construction and len(drawn) == 4" in _function("sketch_rectangle")

    def test_the_geometry_is_drawn_before_anything_is_named(self) -> None:
        body = _function("sketch_rectangle")
        assert body.index("self._draw(") < body.index("_name_the_sides(")


class TestTheCallerIsToldWhatItCanNowDo:
    def test_the_result_names_the_parameters(self) -> None:
        assert '"dimensions_named"' in _function("sketch_rectangle")

    def test_the_note_points_at_set_parameter_not_at_redrawing(self) -> None:
        """The whole point of the change: converge by driving the dimension,
        not by rebuilding the sketch every iteration."""
        body = _function("sketch_rectangle")
        assert "catia_set_parameter" in body
        assert "rather " in body and "than redrawing" in body


class TestTheSignatureStaysCompatible:
    def test_dimension_names_is_optional(self) -> None:
        parameters = inspect.signature(sketcher.SketcherMixin.sketch_rectangle).parameters
        assert parameters["dimension_names"].default is None

    def test_every_previous_argument_survives(self) -> None:
        """A widened method is safe; a narrowed one breaks the registry, which
        `test_backend_signatures.py` is the general guard for."""
        parameters = set(
            inspect.signature(sketcher.SketcherMixin.sketch_rectangle).parameters
        )
        assert {
            "width_mm",
            "height_mm",
            "at",
            "rotation_deg",
            "plane",
            "sketch",
            "construction",
        } <= parameters

    @pytest.mark.parametrize("name", ["width_mm", "height_mm"])
    def test_the_sizes_are_still_required(self, name: str) -> None:
        parameter = inspect.signature(sketcher.SketcherMixin.sketch_rectangle).parameters[name]
        assert parameter.default is inspect.Parameter.empty
