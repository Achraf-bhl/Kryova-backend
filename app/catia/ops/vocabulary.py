"""The shared value vocabulary: what a plane, a face, an edge or a support is.

Everything here is about *reference* — how the model names a piece of geometry
it cannot see. That naming scheme is the real ceiling on what the tool set can
express, and widening it here widens every operation at once.

The change that matters: **a support is a name, not an enum member.** The old
tool set accepted `plane ∈ {XY, YZ, ZX}`, so no sketch could ever sit on an
offset plane, a face, or a plane through three points — the vocabulary had no
way to say it, however capable the CATIA underneath was. `support()` accepts
the three origin planes *or* the name of any plane the model has since created,
and the daemon resolves the string one way or the other. The enum is kept as
`ORIGIN_PLANES` because it is still the right answer for a pattern direction,
where only the three canonical frames are meaningful.
"""

from __future__ import annotations

from typing import Any, Final

from app.catia.ops import limits
from app.catia.ops.spec import one_of

#: CATIA's three standard reference planes, in `Part.OriginElements` order.
ORIGIN_PLANES: Final = ("XY", "YZ", "ZX")

#: The six plane names a pull direction may still be spelled as, newest first in
#: importance: the **vector** is what `pull_direction` advertises and these are the
#: spellings that keep working beside it.
PULL_DIRECTION_NAMES: Final = ("XY", "YZ", "ZX", "-XY", "-YZ", "-ZX")

#: Semantic faces of the part's bounding box, in the part's own frame. Still
#: useful shorthand — "the top face" is what an engineer says — but no longer
#: the *only* way to name a face; see `face_reference`.
NAMED_FACES: Final = ("top", "bottom", "front", "back", "left", "right")

#: Coarse positions on a face. Retained for the cases where they read better
#: than coordinates ("a hole in each corner"), but every operation that accepts
#: one now also accepts an explicit point.
FACE_POSITIONS: Final = ("center", "front_left", "front_right", "back_left", "back_right")

#: Named groups of edges on a feature.
EDGE_SELECTORS: Final = ("all", "vertical", "horizontal", "top", "bottom", "convex", "concave")

#: Standard viewpoints for a screenshot.
VIEWPOINTS: Final = ("iso", "front", "back", "top", "bottom", "left", "right")

#: Units a named parameter may carry. CATIA parameters are typed and setting a
#: length in degrees is a silent no-op, so the unit is never optional.
PARAMETER_UNITS: Final = ("mm", "deg", "kg", "mm2", "mm3", "N", "MPa", "deg_c", "s", "")

#: How a pad, pocket or extrude decides where to stop.
LIMIT_TYPES: Final = (
    "dimension",
    "up_to_next",
    "up_to_last",
    "up_to_plane",
    "up_to_surface",
)

#: Which side of its support a one-sided feature is built on.
SIDES: Final = ("normal", "reversed", "both")

#: How a fillet or draft carries across neighbouring faces.
PROPAGATION: Final = ("tangency", "minimal", "intersection")

#: The 2D geometric constraints the Sketcher understands. These are the
#: `catConstraintType` values that carry no dimension — a coincidence is either
#: satisfied or not, there is no number attached.
GEOMETRIC_CONSTRAINTS: Final = (
    "coincidence",
    "concentricity",
    "tangency",
    "parallelism",
    "perpendicularity",
    "horizontal",
    "vertical",
    "symmetry",
    "equidistant",
    "fix",
)

#: The 2D constraints that do carry a number.
DIMENSIONAL_CONSTRAINTS: Final = ("distance", "length", "radius", "diameter", "angle")

#: Boolean operations between two bodies.
BOOLEAN_OPERATIONS: Final = ("add", "remove", "intersect", "union_trim", "assemble")

#: Assembly constraint kinds.
ASSEMBLY_CONSTRAINTS: Final = (
    "coincidence",
    "contact",
    "offset",
    "angle",
    "parallel",
    "perpendicular",
    "fix",
    "fix_together",
)

#: Drawing view kinds a sheet may carry.
DRAWING_VIEWS: Final = (
    "front",
    "projection",
    "auxiliary",
    "isometric",
    "section",
    "section_cut",
    "detail",
    "clipping",
    "broken",
    "breakout",
    "exploded",
    "unfolded",
)

#: Dimension kinds in a drawing.
DIMENSION_KINDS: Final = (
    "length",
    "distance",
    "angle",
    "radius",
    "diameter",
    "chamfer",
    "thread",
    "coordinate",
)


def support(description: str) -> dict[str, Any]:
    """A plane or planar face to build on, named rather than enumerated.

    Accepts `'XY' | 'YZ' | 'ZX'`, the name of a plane the model created
    (`'Plane.1'`), or a named bounding-box face (`'top'`). The daemon resolves
    in that order, and says which it used, so an ambiguous name is visible in
    the result rather than silently preferred.
    """
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": limits.MAX_NAME_CHARS,
        "description": (
            f"{description} One of 'XY', 'YZ', 'ZX'; or the name of a plane you created "
            "(e.g. 'Plane.1'); or a bounding-box face name "
            f"({', '.join(NAMED_FACES)})."
        ),
    }


def origin_plane(description: str) -> dict[str, Any]:
    """One of the three canonical planes, where only those are meaningful.

    A pattern takes both its directions from one plane — the first in-plane
    axis, then the second — so naming the plane names the directions. A
    user-created plane has no such convention, which is why this stays closed
    where `support` is open.
    """
    return one_of(ORIGIN_PLANES, f"{description} One of: {', '.join(ORIGIN_PLANES)}.")


def pull_direction(description: str) -> dict[str, Any]:
    """Which way a straight-pull tool comes off — a vector, or a plane name.

    **A union, declared rather than half-honoured** (THE QUEUE E10, 2026-09-17).
    Three things forced this exact shape and each of them rules out something
    simpler:

    1. **Not `origin_plane`.** That names a *plane*, and a plane has no side, so
       a mould pulled along −Z — half of every two-part tool — was a question the
       draft analysis had no way to be asked. Its other five users are genuine
       planes (a pattern's grid, a circle's plane, a sketch plane) where a sign is
       meaningless, so widening it would let `-XY` be offered as a sketch plane.
    2. **The vector comes first, because `catia_draft` already takes one.** That
       operation *creates* the taper this one *measures*, and two vocabularies for
       one physical quantity is how an agent drafts along `[0, 0, -1]` and then
       cannot ask about what it just built.
    3. **The names stay, and they stay in the schema rather than as a quiet
       accept-list.** `app/catia/validation.py` checks arguments against this
       document before the backend sees them, so a spelling the schema does not
       declare is refused two storeys above the handler that would have accepted
       it — an accept-list alone would have been unreachable code with a passing
       test behind it. The CATIA KB and the manuals talk about origin planes
       constantly, so a model writing `"XY"` here is following the documentation.

    `nonZero` is carried for the vector arm; the validator applies it only to a
    list, so a name is unaffected.
    """
    return {
        "type": ["array", "string"],
        "minItems": 3,
        "maxItems": 3,
        "items": {"type": "number", "minimum": -1e6, "maximum": 1e6},
        "nonZero": True,
        # **No `enum` here, deliberately.** `validation.validate` applies an enum to
        # whatever it is given, so listing the six names would refuse every *vector*
        # as "not one of: XY, YZ, …" — the union's other arm, rejected by its own
        # constraint. A name the table does not hold is refused one storey down by
        # `_pull_direction`, which can see that it is a name and says so.
        "description": (
            f"{description} A direction vector such as [0, 0, 1] or [0, 0, -1] — the "
            f"same one catia_draft takes — or a plane name: "
            f"{', '.join(PULL_DIRECTION_NAMES)}. The vector's length is not used."
        ),
    }


def face_reference(description: str) -> dict[str, Any]:
    """A face, named semantically or by the feature that created it."""
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": limits.MAX_NAME_CHARS,
        "description": (
            f"{description} Either a bounding-box face name "
            f"({', '.join(NAMED_FACES)}) or a face reported by catia_list_faces."
        ),
    }


def edge_reference(description: str) -> dict[str, Any]:
    """An edge, named semantically or by id from catia_list_edges."""
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": limits.MAX_NAME_CHARS,
        "description": (
            f"{description} Either an edge group ({', '.join(EDGE_SELECTORS)}) or a "
            "specific edge id reported by catia_list_edges."
        ),
    }


def element_reference(description: str) -> dict[str, Any]:
    """Any named element: a sketch, feature, body, surface, curve or point.

    **The naming rule is stated once, in the system prompt, and not here.**
    It used to be appended to every one of these -- "Name it exactly as
    catia_list_features or the tool that created it reported it (e.g.
    'Sketch.1', 'Pad.1', 'Plane.2')" -- which is 118 characters repeated 182
    times across the registry.

    Measured on ladder prompt H4 turn 2, 2026-09-06, on the seat: the model's
    prompt was 24,798 tokens, of which the tool schemas were **14,866 -- 60% of
    it** -- and each step cost 15-26 s against 0.3-1.1 s for the CATIA calls.
    The model, not COM, is the whole latency of a turn, and the schemas are the
    bulk of the model's prompt. The repetition was also pushing a 32,768-token
    window to 76% full, which is where Ollama starts truncating from the front
    in silence.

    A rule that applies to every reference belongs in the frozen system prefix,
    where it is stated once and cached, not in the per-turn payload where it is
    re-sent on every step of every turn.
    """
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": limits.MAX_NAME_CHARS,
        "description": description,
    }
