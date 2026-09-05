"""Pointing at geometry by what it *is*, not by the number it happens to have.

Master plan Phase 2.1 — the single most limiting constraint in the authoring vocabulary
until now. A design could say `edges: "vertical"` and little else; it could not say *the
edges longer than 10 mm*, *the faces normal to +Z*, or *the Ø6 bores*. Every real part
needs that, and the alternative — face indices — is exactly the fragility Layer B exists
to remove, because an index means something different after any upstream edit.

**The vocabulary deliberately mirrors `app/solve/types.py`.** The FEA layer already
selects regions with `{"type": "face", "axis": "z", "side": "min"}`, and that spelling is
a project rule: *region selection is by geometric selector, never by face id*. Selecting a
face to fillet and selecting a face to load are the same question asked of different
layers, so an engineer who has learned one already knows the other. What differs is the
target — the FEA selectors resolve to mesh **nodes**, these resolve to B-rep **faces and
edges** — which is why this is a parallel vocabulary and not a shared implementation.

**Predicates compose by conjunction, and that is a deliberate limit.** Every field given
must hold. There is no `or`, no nesting, no negation: a selector language with those
becomes a query language, and a query language over geometry is a thing to design on
purpose rather than to arrive at by accretion. Two disjoint sets of edges are two
operations, which is also how a person would describe them.

**An empty match is an error at the call site, never a silent no-op.** A fillet that
matched nothing and reported success leaves a part that is wrong in a way no assertion
about the fillet would catch, because the fillet is not there to be measured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final, Literal

from app.kernel.errors import GeometryError

#: Axis words, in the spelling `app/solve/types.py` uses.
AXES: Final[tuple[str, ...]] = ("x", "y", "z")

#: Which extreme along an axis, in the spelling `app/solve/types.py` uses.
SIDES: Final[tuple[str, ...]] = ("min", "max")

#: Index of each axis in a coordinate triple.
AXIS_INDEX: Final[dict[str, int]] = {"x": 0, "y": 1, "z": 2}

#: How close a face normal must be to a named direction to count as facing it. Five
#: degrees is loose enough to catch a face that is nominally flat but carries a draft
#: angle, tight enough that an adjacent wall never qualifies.
DEFAULT_NORMAL_TOLERANCE_DEG: Final = 5.0

#: How close to parallel or perpendicular an entity must be to count as either. Tighter
#: than the normal tolerance on purpose: "facing roughly +Z" is a fuzzy claim about which
#: way a face looks, while "parallel to Z" is a sharp claim about its orientation, and a
#: wall carrying 3° of draft genuinely is *not* parallel to the pull any more — which is
#: the whole point of having drafted it.
DEFAULT_ANGLE_TOLERANCE_DEG: Final = 1.0

#: How close a measured diameter must be to a requested one, in millimetres. A bore is
#: modelled at its nominal size, so this only absorbs kernel noise — not a fit
#: allowance, which is a real dimensional difference and should not silently match.
DEFAULT_DIAMETER_TOLERANCE_MM: Final = 1e-6

#: Named directions a predicate may point at, as unit vectors.
DIRECTIONS: Final[dict[str, tuple[float, float, float]]] = {
    "+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0),
}

EntityKind = Literal["edge", "face"]


@dataclass(frozen=True)
class Box:
    """An axis-aligned region, in millimetres. Mirrors `solve.types.BoxSelector`."""

    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    def contains(self, point: tuple[float, float, float]) -> bool:
        return all(
            self.minimum[i] - 1e-9 <= point[i] <= self.maximum[i] + 1e-9 for i in range(3)
        )

    @classmethod
    def parse(cls, value: Any) -> Box:
        if not isinstance(value, dict):
            raise GeometryError(
                "A box is {'min': [x, y, z], 'max': [x, y, z]} in millimetres, "
                f"got {value!r}."
            )
        try:
            low = tuple(float(v) for v in value["min"])
            high = tuple(float(v) for v in value["max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GeometryError(
                "A box needs 'min' and 'max', each three numbers in millimetres."
            ) from exc
        if len(low) != 3 or len(high) != 3:
            raise GeometryError("A box's 'min' and 'max' are three numbers each.")
        if any(high[i] < low[i] for i in range(3)):
            raise GeometryError(
                f"This box has max below min on at least one axis: {low} .. {high}."
            )
        return cls(minimum=low, maximum=high)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Predicate:
    """What a design means when it points at some edges or faces.

    Every field that is set must hold — see the conjunction note in the module
    docstring. A predicate with no field set matches everything of its kind, which is
    what the bare word `all` means.
    """

    kind: EntityKind

    # -- shared ------------------------------------------------------------
    #: Only entities lying at the extreme of the shape along an axis. `axis="z",
    #: side="max"` is "on the top face", in the FEA layer's spelling.
    axis: str | None = None
    side: str | None = None

    #: Only entities entirely inside this region.
    inside: Box | None = None

    #: Restrict to the entities one named feature contributed — master plan 2.2, the
    #: `feature#selector` spelling. `{"of": "boss", "axis": "z", "side": "max"}` is *the
    #: top of the boss*, not the top of the part, and on a part where the boss is not the
    #: highest thing those are different faces.
    #:
    #: Evaluated **first**, so every other field then applies within the feature. That
    #: also makes `axis`/`side` mean "the extreme of the feature", which is what someone
    #: writing `boss#top` means and is not what "the extreme of the part" would give.
    of: str | None = None

    #: Orientation relative to a direction, and the pair that makes "the vertical walls"
    #: sayable. **For a face these are about its plane, not its normal**, which is the
    #: convention every CAD system uses and the opposite of what the arithmetic does:
    #: a face *parallel to* Z has a normal perpendicular to Z. So `parallel_to="z"` is
    #: the four walls of a block and `perpendicular_to="z"` is its top and bottom.
    #:
    #: For an edge they are about its direction, tested along its whole length — so a
    #: horizontal circle is perpendicular to Z everywhere and matches, while an arc that
    #: climbs matches neither, which is correct and is what a single end-to-end
    #: comparison would get wrong.
    #:
    #: Unsigned in both cases: a face parallel to Z is parallel to it whichever way it
    #: looks, and requiring a sign would make "the walls" four separate selections again.
    parallel_to: str | tuple[float, float, float] | None = None
    perpendicular_to: str | tuple[float, float, float] | None = None
    angle_tolerance_deg: float = DEFAULT_ANGLE_TOLERANCE_DEG

    # -- edges -------------------------------------------------------------
    longer_than_mm: float | None = None
    shorter_than_mm: float | None = None

    #: `True` selects only circular edges, `False` only non-circular.
    circular: bool | None = None

    #: `True` selects convex edges (material on the inside of the angle), `False`
    #: concave. This is what "fillet the outside corners" means.
    convex: bool | None = None

    # -- faces -------------------------------------------------------------
    #: A named direction (`"+z"`) or a vector, that the face's outward normal follows.
    normal: str | tuple[float, float, float] | None = None
    normal_tolerance_deg: float = DEFAULT_NORMAL_TOLERANCE_DEG

    #: `True` selects cylindrical faces (bores, bosses), `False` everything else.
    cylindrical: bool | None = None
    planar: bool | None = None

    #: Cylinder diameter, for picking one bore size out of many.
    diameter_mm: float | None = None
    diameter_tolerance_mm: float = DEFAULT_DIAMETER_TOLERANCE_MM

    larger_than_mm2: float | None = None
    smaller_than_mm2: float | None = None

    def describe(self) -> str:
        """The predicate in words, for an error that has to say what matched nothing."""
        parts: list[str] = []
        if self.axis and self.side:
            parts.append(f"at the {self.side} of {self.axis}")
        if self.of is not None:
            parts.append(f"belonging to {self.of}")
        if self.inside is not None:
            parts.append("inside the given box")
        if self.parallel_to is not None:
            parts.append(f"parallel to {self.parallel_to}")
        if self.perpendicular_to is not None:
            parts.append(f"perpendicular to {self.perpendicular_to}")
        if self.longer_than_mm is not None:
            parts.append(f"longer than {self.longer_than_mm} mm")
        if self.shorter_than_mm is not None:
            parts.append(f"shorter than {self.shorter_than_mm} mm")
        if self.circular is not None:
            parts.append("circular" if self.circular else "not circular")
        if self.convex is not None:
            parts.append("convex" if self.convex else "concave")
        if self.normal is not None:
            parts.append(f"facing {self.normal}")
        if self.cylindrical is not None:
            parts.append("cylindrical" if self.cylindrical else "not cylindrical")
        if self.planar is not None:
            parts.append("planar" if self.planar else "not planar")
        if self.diameter_mm is not None:
            parts.append(f"of diameter {self.diameter_mm} mm")
        if self.larger_than_mm2 is not None:
            parts.append(f"larger than {self.larger_than_mm2} mm²")
        if self.smaller_than_mm2 is not None:
            parts.append(f"smaller than {self.smaller_than_mm2} mm²")
        return f"{self.kind}s " + (" and ".join(parts) if parts else "(all)")


#: Fields each entity kind accepts, so a predicate meant for one is not silently
#: accepted for the other. Asking for a face's `normal` on an edge is a mistake worth
#: catching at the boundary, not something to ignore.
_ORIENTATION_FIELDS: Final[frozenset[str]] = frozenset(
    {"parallel_to", "perpendicular_to", "angle_tolerance_deg", "of"}
)
_EDGE_FIELDS: Final[frozenset[str]] = frozenset(
    {"axis", "side", "inside", "longer_than_mm", "shorter_than_mm", "circular", "convex"}
) | _ORIENTATION_FIELDS
_FACE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "axis", "side", "inside", "normal", "normal_tolerance_deg", "cylindrical",
        "planar", "diameter_mm", "diameter_tolerance_mm", "larger_than_mm2",
        "smaller_than_mm2",
    }
) | _ORIENTATION_FIELDS


def parse(value: Any, *, kind: EntityKind) -> Predicate:
    """Read a predicate from a design's argument value.

    `{"type": "edge", "longer_than_mm": 10}` — the `type` is optional when the caller
    already knows which entity kind it is asking about, and is checked when present so
    a face predicate handed to an edge argument is refused rather than half-applied.
    """
    if not isinstance(value, dict):
        raise GeometryError(
            f"A {kind} predicate is an object like "
            f'{{"type": "{kind}", ...}}, got {value!r}.'
        )

    declared = value.get("type")
    if declared is not None and str(declared) != kind:
        raise GeometryError(
            f"This argument selects {kind}s, but the predicate says "
            f"type={declared!r}. A face predicate cannot select edges."
        )

    allowed = _EDGE_FIELDS if kind == "edge" else _FACE_FIELDS
    unknown = set(value) - allowed - {"type"}
    if unknown:
        raise GeometryError(
            f"A {kind} predicate does not accept {sorted(unknown)}. It accepts: "
            f"{', '.join(sorted(allowed))}."
        )

    axis, side = _parse_extreme(value)
    fields: dict[str, Any] = {"kind": kind, "axis": axis, "side": side}

    if "inside" in value:
        fields["inside"] = Box.parse(value["inside"])

    if "of" in value:
        owner = value["of"]
        if not isinstance(owner, str) or not owner.strip():
            raise GeometryError(
                f"'of' names the feature whose entities to select, got {owner!r}."
            )
        fields["of"] = owner.strip()

    for name in ("longer_than_mm", "shorter_than_mm", "larger_than_mm2",
                 "smaller_than_mm2", "diameter_mm"):
        if name in value:
            fields[name] = _positive(value[name], name)

    for name in ("circular", "convex", "cylindrical", "planar"):
        if name in value:
            fields[name] = bool(value[name])

    if "normal" in value:
        fields["normal"] = _parse_direction(value["normal"])
    for name in ("parallel_to", "perpendicular_to"):
        if name in value:
            fields[name] = _parse_direction(value[name], allow_unsigned=True)
    for name in ("normal_tolerance_deg", "diameter_tolerance_mm", "angle_tolerance_deg"):
        if name in value:
            fields[name] = _positive(value[name], name)

    if fields.get("parallel_to") is not None and fields.get("perpendicular_to") is not None:
        raise GeometryError(
            "A predicate cannot ask for entities both parallel and perpendicular to a "
            "direction — nothing is both. Use one, or two separate operations."
        )

    return Predicate(**fields)


def _parse_extreme(value: dict[str, Any]) -> tuple[str | None, str | None]:
    axis = value.get("axis")
    side = value.get("side")
    if axis is None and side is None:
        return (None, None)
    if axis is None or side is None:
        raise GeometryError(
            "'axis' and 'side' go together: 'axis' says which direction, 'side' says "
            "which end of it ('min' or 'max')."
        )
    axis, side = str(axis).lower(), str(side).lower()
    if axis not in AXES:
        raise GeometryError(f"{axis!r} is not an axis. Use one of: {', '.join(AXES)}.")
    if side not in SIDES:
        raise GeometryError(f"{side!r} is not a side. Use one of: {', '.join(SIDES)}.")
    return (axis, side)


def _parse_direction(
    value: Any, *, allow_unsigned: bool = False
) -> str | tuple[float, float, float]:
    """A named direction or a vector.

    `allow_unsigned` additionally accepts a bare axis letter — `"z"` rather than `"+z"`.
    Only the orientation fields take it, and the reason is that a sign there would be
    meaningless: a wall parallel to Z is parallel to it whichever way the wall faces, so
    demanding `"+z"` would imply a distinction that does not exist. `normal` keeps
    requiring a sign because for it the sign is the entire question.
    """
    if isinstance(value, str):
        word = value.lower()
        if allow_unsigned and word in AXES:
            return f"+{word}"
        if word not in DIRECTIONS:
            allowed = ", ".join(sorted(DIRECTIONS))
            extra = f", a bare axis ({', '.join(AXES)})" if allow_unsigned else ""
            raise GeometryError(
                f"{value!r} is not a direction. Use one of {allowed}{extra}, or a "
                "vector [x, y, z]."
            )
        return word
    if isinstance(value, (list, tuple)) and len(value) == 3:
        vector = tuple(float(component) for component in value)
        if math.isclose(sum(c * c for c in vector), 0.0):
            raise GeometryError("A direction cannot be the zero vector.")
        return vector  # type: ignore[return-value]
    raise GeometryError(
        f"A direction is a word like '+z' or a vector [x, y, z], got {value!r}."
    )


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{name} must be a number, got {value!r}.") from exc
    if number <= 0:
        raise GeometryError(f"{name} must be positive, got {number}.")
    return number


def unit(direction: str | tuple[float, float, float]) -> tuple[float, float, float]:
    """A direction as a unit vector, whether it was named or given."""
    vector = DIRECTIONS[direction] if isinstance(direction, str) else direction
    length = math.sqrt(sum(component * component for component in vector))
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def is_predicate(value: Any) -> bool:
    """Is this argument a predicate rather than one of the vocabulary words?"""
    return isinstance(value, dict)


__all__ = [
    "AXES",
    "AXIS_INDEX",
    "DEFAULT_ANGLE_TOLERANCE_DEG",
    "DEFAULT_DIAMETER_TOLERANCE_MM",
    "DEFAULT_NORMAL_TOLERANCE_DEG",
    "DIRECTIONS",
    "SIDES",
    "Box",
    "EntityKind",
    "Predicate",
    "is_predicate",
    "parse",
    "unit",
]
