"""Resolving what a design points at — vocabulary words and geometric predicates.

Two ways to name geometry, and both go through here:

* **A vocabulary word** — `all`, `vertical`, `horizontal`, `convex`, `concave`, `top`,
  `bottom`. Declared in `app.catia.ops.vocabulary`, which is the single source the tool
  schemas, the daemon's validation table and the model's prompt are all generated from.
  Re-listing them here would create a second, quietly diverging vocabulary whose first
  symptom is a design that validates and then selects nothing.
* **A predicate** — `{"longer_than_mm": 10}`, `{"cylindrical": true, "diameter_mm": 6}`.
  Master plan Phase 2.1. This is what a real part needs, and what removes the fragility
  of pointing at geometry by the number it happens to have.

**The words are now defined in terms of the predicates.** `convex` is
`{"convex": true}`; `top` is `{"axis": "z", "side": "max"}`. That is what let three of
the four previously-refused words start working, and it means there is one evaluator to
be right rather than two that agree until they do not.
"""

from __future__ import annotations

import math
from typing import Any, Final

from app.catia.ops import vocabulary
from app.kernel.errors import GeometryError
from app.kernel.occt.binding import symbol
from app.kernel.occt.resolve import require_matches, resolve
from app.kernel.occt.topology import edges
from app.kernel.selection import EntityKind, Predicate, is_predicate, parse

#: How far an edge's curve must climb before it counts as vertical. Well under any real
#: feature size, well over the noise of evaluating a curve — which, unlike a bounding
#: box, has no built-in tolerance to clear.
VERTICAL_TOLERANCE_MM: Final = 1e-9

#: Points sampled along an edge to measure how far it climbs. Two would be enough for a
#: line and wrong for an arc that rises and comes back down.
_DIRECTION_SAMPLES: Final = 8

#: The vocabulary word meaning "every entity", taken from the shared declaration.
ALL: Final = vocabulary.EDGE_SELECTORS[0]

#: Vocabulary words expressed as predicates. Defining the words in terms of predicates
#: rather than as separate code paths is what keeps the two from drifting.
#:
#: `vertical` and `horizontal` are absent because they are about an edge's *direction*,
#: which the predicate vocabulary does not describe — a direction predicate for edges is
#: worth adding when something needs it, and inventing it unused would be worse.
_WORD_PREDICATES: Final[dict[str, dict[str, Any]]] = {
    "top": {"axis": "z", "side": "max"},
    "bottom": {"axis": "z", "side": "min"},
    "convex": {"convex": True},
    "concave": {"convex": False},
}

#: Words this backend answers by direction rather than by predicate.
_DIRECTIONAL_WORDS: Final[tuple[str, ...]] = ("vertical", "horizontal")

#: What separates a feature from the entity of it being named — master plan 2.2.
#: `boss#top` is the top of the boss; `top` alone is the top of the part.
SUB_ENTITY_MARK: Final = "#"


def parse_sub_entity(text: str, *, tool: str = "this operation") -> dict[str, Any]:
    """`feature#selector` → the equivalent predicate with `of` set.

    One spelling, two forms: `boss#top` uses a vocabulary word, and
    `boss#{"cylindrical": true}` would use a predicate — but only the word form is worth
    a shorthand, because anything longer is more readable written as a predicate with
    `of` directly. So this expands a word and refuses the rest, rather than growing a
    second parser for embedded JSON.
    """
    feature, _, word = text.partition(SUB_ENTITY_MARK)
    feature, word = feature.strip(), word.strip()

    if not feature or not word:
        raise GeometryError(
            f"{text!r} is not a sub-entity reference. It is written "
            f"feature{SUB_ENTITY_MARK}selector, for example "
            f"boss{SUB_ENTITY_MARK}top — the top of the boss, as against 'top', which "
            "is the top of the whole part."
        )
    if SUB_ENTITY_MARK in word:
        raise GeometryError(
            f"{text!r} has more than one {SUB_ENTITY_MARK}. A sub-entity reference names "
            "one feature and one selector; an entity of an entity is not a thing this "
            "vocabulary has."
        )

    template = _WORD_PREDICATES.get(word.lower())
    if template is None:
        known = ", ".join(sorted(_WORD_PREDICATES))
        raise GeometryError(
            f"{word!r} is not a selector word, so {text!r} cannot be resolved. After "
            f"the {SUB_ENTITY_MARK} use one of: {known} — or write the predicate out "
            f'with the feature named, as {{"of": "{feature}", ...}}.'
        )
    return {**template, "of": feature}


def select_edges(
    shape: Any, selector: Any, *, tool: str = "this operation", document: Any = None
) -> list[Any]:
    """Resolve an edge selector — a word, a predicate, or `feature#word` — to real edges."""
    return _select(shape, selector, kind="edge", tool=tool, document=document)


def select_faces(
    shape: Any, selector: Any, *, tool: str = "this operation", document: Any = None
) -> list[Any]:
    """Resolve a face selector — a word, a predicate, or `feature#word` — to real faces."""
    return _select(shape, selector, kind="face", tool=tool, document=document)


def _select(
    shape: Any, selector: Any, *, kind: EntityKind, tool: str, document: Any = None
) -> list[Any]:
    if selector in (None, "", ALL):
        return resolve(shape, Predicate(kind=kind))

    if isinstance(selector, str) and SUB_ENTITY_MARK in selector:
        selector = parse_sub_entity(selector, tool=tool)

    if is_predicate(selector):
        predicate = parse(selector, kind=kind)
        return require_matches(resolve(shape, predicate, document), predicate, tool)

    if isinstance(selector, (list, tuple)):
        # A list of predicates would be a disjunction, which the vocabulary
        # deliberately does not have (see app.kernel.selection). A list of *names* needs
        # `feature#selector` resolution, which the document owns rather than this.
        raise GeometryError(
            f"{tool} was given a list where one selector was expected. Use a single "
            "predicate, or run the operation once per group — the selector vocabulary "
            "has no 'or' on purpose."
        )

    word = str(selector).lower()

    if kind == "edge" and word in _DIRECTIONAL_WORDS:
        return require_matches(
            _select_by_direction(shape, word), Predicate(kind=kind), tool
        )

    template = _WORD_PREDICATES.get(word)
    if template is not None:
        predicate = parse(template, kind=kind)
        return require_matches(resolve(shape, predicate), predicate, tool)

    known = ", ".join(vocabulary.EDGE_SELECTORS)
    raise GeometryError(
        f"{word!r} is not a selector word. The vocabulary is: {known} — or give a "
        'predicate such as {"longer_than_mm": 10}.'
    )


def _select_by_direction(shape: Any, word: str) -> list[Any]:
    """Vertical or horizontal edges, by how much the edge's own curve changes height.

    **Two obvious implementations are wrong, and both were tried.**

    *Comparing the two endpoints* fails on a closed edge. A full circle has exactly one
    vertex — its seam — so there is no second endpoint to compare, and the rim of a
    cylinder is neither classified nor skipped honestly. (It appeared to work while
    `topology.explore` was double-counting vertices: the circle reported the same vertex
    twice, the height difference came out zero, and it was called horizontal by
    accident. De-duplicating the traversal removed the accident and the bug surfaced.)

    *Taking the edge's bounding box* fails on tolerance. OCCT's `Bnd_Box` carries about
    ±1e-7 mm even when asked for a tight box with `SetGap(0)`, so a perfectly flat circle
    measures 2e-7 mm tall — above any threshold tight enough to be meaningful, and every
    rim on every part comes back vertical.

    So the curve itself is sampled. It is exact, it needs no special case for closed
    edges, and it is right for arcs and splines as well as lines — an edge that climbs in
    the middle and returns is correctly not horizontal, which neither of the other two
    methods could see.
    """
    wants_vertical = word == "vertical"
    chosen: list[Any] = []
    for edge in edges(shape):
        rise = _height_range(edge)
        if rise is None:
            continue
        if (rise > VERTICAL_TOLERANCE_MM) == wants_vertical:
            chosen.append(edge)
    return chosen


def _height_range(edge: Any) -> float | None:
    """How far the edge's curve travels in Z, or None if it has no evaluable curve."""
    curve = symbol("BRepAdaptor_Curve")(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    if not (math.isfinite(first) and math.isfinite(last)):
        return None

    span = last - first
    heights = [
        curve.Value(first + span * index / _DIRECTION_SAMPLES).Z()
        for index in range(_DIRECTION_SAMPLES + 1)
    ]
    return max(heights) - min(heights)


def supported_words() -> tuple[str, ...]:
    """Every vocabulary word this backend can now decide."""
    return (ALL, *_DIRECTIONAL_WORDS, *sorted(_WORD_PREDICATES))


def unsupported_words() -> tuple[str, ...]:
    """Vocabulary words still not decidable here. Empty is the goal, and now the truth."""
    return tuple(word for word in vocabulary.EDGE_SELECTORS if word not in supported_words())


__all__ = [
    "ALL",
    "VERTICAL_TOLERANCE_MM",
    "select_edges",
    "select_faces",
    "supported_words",
    "unsupported_words",
]
