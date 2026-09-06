"""A parameter is findable by the name a person says, not only by its full path.

Measured on the seat, 2026-09-06, ladder prompt S1 run 2 -- the first run after
`catia_sketch_rectangle` started naming its width and height. The parameters
existed this time and the agent still could not drive them:

    CATIA: set parameter -- No parameter named 'width' in this part.
    CATIA: set parameter -- No parameter named
        'Part1\\\\\\\\Corps principal\\\\\\\\Extrusion.2\\\\\\\\EpaisFin2' in this part.

Two separate defects in three rounds.

**The short name.** CATIA's real names are long, localised paths --
`Part1\\Corps principal\\Extrusion.2\\Sketch.3\\width\\Longueur` -- and
`catia_list_parameters` honestly reports them. Requiring one verbatim is
unusable: the agent asked for `width`, which is the name *it* had given the
dimension, and was turned away. A unique path segment now resolves.

**The backslashes.** The second name came back with four backslashes where
CATIA has one. Nothing in the chain is individually wrong -- the tool result is
JSON, JSON escapes a backslash, and a model copying the rendered string copies
what it was shown. CATIA parameter paths are the only names in this product
that contain a backslash, so this is the only place it bites, and the fix
belongs at the lookup rather than in every model that will ever read one.

Ambiguity is refused, never resolved: two matches means picking one would
silently drive the wrong dimension, which is the failure this layer exists to
prevent.

Offline: fake COM collections, no CATIA.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402

SEAT = [
    r"Part1\Corps principal\Extrusion.2\Première limite\Longueur",
    r"Part1\Corps principal\Extrusion.2\Sketch.3\width\Longueur",
    r"Part1\Corps principal\Extrusion.2\Sketch.3\height\Longueur",
    r"Part1\Corps principal\Extrusion.2\EpaisFin2",
]


class _Parameter:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Parameters:
    """Stands in for CATIA's Parameters collection, which is 1-indexed and
    raises rather than returning None for a name it does not hold."""

    def __init__(self, names: list[str]) -> None:
        self._names = list(names)

    @property
    def Count(self) -> int:  # noqa: N802 - COM spelling
        return len(self._names)

    def Item(self, key):  # noqa: N802 - COM spelling
        if isinstance(key, int):
            return _Parameter(self._names[key - 1])
        for name in self._names:
            if name == key:
                return _Parameter(name)
        raise RuntimeError(f"no parameter {key!r}")


@pytest.fixture
def parameters() -> _Parameters:
    return _Parameters(SEAT)


def find(parameters: _Parameters, name: str):
    return CatiaCom._find_parameter(CatiaCom, parameters, name)  # type: ignore[arg-type]


class TestTheFullPathAlwaysWins:
    def test_an_exact_name_resolves(self, parameters: _Parameters) -> None:
        assert find(parameters, SEAT[1]).Name == SEAT[1]

    def test_it_is_tried_before_anything_clever(self, parameters: _Parameters) -> None:
        """A caller that has the real path must never be surprised by a guess."""
        exact = _Parameters([r"A\width", r"B\width\Longueur"])
        assert find(exact, r"A\width").Name == r"A\width"


class TestTheShortName:
    def test_a_unique_segment_resolves(self, parameters: _Parameters) -> None:
        """The one that failed on the seat."""
        assert find(parameters, "width").Name == SEAT[1]

    def test_the_other_dimension_too(self, parameters: _Parameters) -> None:
        assert find(parameters, "height").Name == SEAT[2]

    def test_case_does_not_matter(self, parameters: _Parameters) -> None:
        assert find(parameters, "WIDTH").Name == SEAT[1]

    def test_surrounding_space_does_not_matter(self, parameters: _Parameters) -> None:
        assert find(parameters, "  width  ").Name == SEAT[1]

    def test_a_localised_segment_works_the_same(self, parameters: _Parameters) -> None:
        """Nothing here is an English-only rule; the seat is French."""
        assert find(parameters, "Première limite").Name == SEAT[0]

    def test_a_partial_word_is_not_a_match(self, parameters: _Parameters) -> None:
        """Segment equality, not substring: `wid` must not silently drive
        `width`, and `Longueur` must not match four parameters at once by
        accident of being a suffix."""
        with pytest.raises(CatiaOperationError):
            find(parameters, "wid")


class TestTheBackslashes:
    def test_a_json_doubled_path_resolves(self, parameters: _Parameters) -> None:
        doubled = SEAT[3].replace("\\", "\\\\")
        assert find(parameters, doubled).Name == SEAT[3]

    def test_the_quadrupled_one_measured_on_the_seat_resolves(
        self, parameters: _Parameters
    ) -> None:
        quadrupled = SEAT[3].replace("\\", "\\\\\\\\")
        assert find(parameters, quadrupled).Name == SEAT[3]

    def test_a_single_backslash_path_is_untouched(self) -> None:
        assert CatiaCom._unescape_path(SEAT[0]) == SEAT[0]

    def test_collapsing_is_safe_because_a_path_has_no_empty_segment(self) -> None:
        assert CatiaCom._unescape_path("a\\\\b") == "a\\b"


class TestAmbiguityIsRefusedNotResolved:
    def test_two_matches_raise(self) -> None:
        """Picking one would silently drive the wrong dimension."""
        both = _Parameters([r"Part1\Sketch.1\width\Longueur", r"Part1\Sketch.2\width\Longueur"])
        with pytest.raises(CatiaOperationError, match="matches 2 parameters"):
            find(both, "width")

    def test_the_refusal_names_the_candidates(self) -> None:
        """So the next call can be right, rather than being another guess."""
        both = _Parameters([r"Part1\Sketch.1\width\Longueur", r"Part1\Sketch.2\width\Longueur"])
        with pytest.raises(CatiaOperationError) as raised:
            find(both, "width")
        assert "Sketch.1" in str(raised.value)
        assert "Sketch.2" in str(raised.value)

    def test_nothing_found_says_how_names_work(self, parameters: _Parameters) -> None:
        with pytest.raises(CatiaOperationError) as raised:
            find(parameters, "thickness")
        message = str(raised.value)
        assert "catia_list_parameters" in message
        assert "one segment" in message
