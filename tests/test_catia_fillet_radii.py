"""`catia_fillet` honours the per-edge radius list its own schema promises.

Measured on the seat, 2026-09-06, running ladder prompt H1 (a spec sheet asking
for R10 on all four corners). `catia_fillet`'s server-side schema says in words
that "radius_mm may be a list, one per selected edge in selection order". The
model read that, sent the list, and got:

    TypeError while running catia_fillet: unsupported format string passed to
    list.__format__

Two failures stacked. `catia_com.fillet` did `float(radius_mm)`, which raises on
a list -- so the advertised capability was never implemented -- and then its own
`except` handler raised a *second* time rendering `{radius_mm:g}` for a list, so
the real cause never reached the model or the log. What the agent saw was an
internal TypeError with no remedy in it, and the run died there.

The lesson is the one CLAUDE.md states for the technology register, applied to
our own surface: a schema that advertises a capability the bridge does not
implement is worse than one that never offered it, because the model is being
told the truth by one half of the system and refused by the other. These tests
pin both halves against each other.

Runs against the mock backend: no CATIA, no seat.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.catia.tool_specs import get_spec
from app.catia.validation import validate

# The daemon is not an installed package; the same insertion every other
# bridge test uses (see tests/test_catia_daemon.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402


def test_the_schema_really_does_promise_a_list() -> None:
    """If this stops being true, the rest of this file is testing a ghost."""
    spec = get_spec("catia_fillet")
    assert spec is not None
    radius = spec.parameters["properties"]["radius_mm"]
    types = radius.get("type")
    assert types is not None
    assert "array" in (types if isinstance(types, list) else [types]), radius


@pytest.mark.parametrize("radius", [10, 10.0, [10, 10, 10, 10]])
def test_both_forms_pass_validation(radius: object) -> None:
    spec = get_spec("catia_fillet")
    assert spec is not None
    validate({"radius_mm": radius, "edges": "vertical"}, spec.parameters)


class TestMockBackend:
    """The mock is the only place this is executable off a seat, and it is the
    backend every CI run and every `--mock` session exercises."""

    @staticmethod
    def _part(tmp_path: Path):
        catia = MockCatia(tmp_path / "catia")
        catia.new_part(name="plate")
        catia.sketch_rectangle(plane="XY", width_mm=120, height_mm=80)
        # 40 thick, not the 15 of the H1 spec sheet: the mock limits a fillet
        # by the part's *smallest* dimension, which for a vertical corner edge
        # is the wrong one -- a corner radius is bounded by the plan size, not
        # the thickness. That is a separate mock-fidelity question and is left
        # alone here rather than quietly worked around, so these tests measure
        # list handling and nothing else.
        catia.pad(sketch="Sketch.1", length_mm=40)
        return catia

    def test_a_single_number_still_works(self, tmp_path: Path) -> None:
        result = self._part(tmp_path).fillet(radius_mm=5, edges="vertical")
        assert result["feature"]

    def test_a_list_is_accepted_and_does_not_raise_a_typeerror(self, tmp_path: Path) -> None:
        """The exact call from H1: one radius per selected edge."""
        result = self._part(tmp_path).fillet(radius_mm=[10, 10, 10, 10], edges="vertical")
        assert result["feature"]

    def test_distinct_radii_are_accepted(self, tmp_path: Path) -> None:
        result = self._part(tmp_path).fillet(radius_mm=[10, 8, 6, 4], edges="vertical")
        assert result["feature"]

    def test_a_wrong_length_list_is_refused_in_words_a_model_can_act_on(self, tmp_path: Path) -> None:
        with pytest.raises(CatiaOperationError) as caught:
            self._part(tmp_path).fillet(radius_mm=[10, 10], edges="vertical")
        message = str(caught.value)
        assert "2 value(s)" in message
        assert "4 edge(s)" in message
        # It must say what to do instead, not merely that it is wrong.
        assert "single number" in message

    def test_an_over_large_radius_is_still_caught_in_the_list_form(self, tmp_path: Path) -> None:
        """The largest radius governs, and the guard must not be lost to the
        new branch: 50 mm on a 15 mm-thick plate consumes the face."""
        with pytest.raises(CatiaOperationError, match="larger than half"):
            self._part(tmp_path).fillet(radius_mm=[50, 2, 2, 2], edges="vertical")

    def test_the_refusal_message_itself_never_raises_on_a_list(self, tmp_path: Path) -> None:
        """The second half of the original bug: the error handler crashed.

        A handler that raises while formatting replaces a message the model
        could act on with an internal TypeError, which is how this defect
        stayed invisible.
        """
        with pytest.raises(CatiaOperationError) as caught:
            self._part(tmp_path).fillet(radius_mm=[99, 99, 99, 99], edges="vertical")
        assert "TypeError" not in str(caught.value)
        assert "__format__" not in str(caught.value)
