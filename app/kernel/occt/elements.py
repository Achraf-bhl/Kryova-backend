"""What a measurement is pointed at — resolving one element reference to one operand.

`catia_measure_between` and `catia_measure_item` take *names*, and a name in a design can
mean four different things: a feature (`boss`), one entity of a feature
(`boss#top` — master plan 2.2), a constructed plane (`boss_top`), or a reference point.
This module is the one place that fork is decided, so the two operations cannot drift
apart on what `boss#top` means.

Three things here are not obvious and are the reason it is a module rather than a helper.

**The entity kind is inferred from the selector word, and the mapping is a decision.**
`boss#top` is the *face* at the top of the boss; `boss#convex` is the *edges* that are
convex. Both spellings are one string with no place to say which, and demanding one would
make the shorthand longer than the predicate it stands in for. So `top`/`bottom`/`all`
resolve faces and `convex`/`concave`/`vertical`/`horizontal` resolve edges — the reading
each word already has everywhere else in the vocabulary.

**A name that means two things is refused, not guessed.** Planes, points, axis systems and
features are separate namespaces in a `PartDocument`, so nothing stops a design from
having a plane and a feature both called `datum`. Picking one by table order would work
until the day it silently measured the wrong thing.

**A reference plane is unbounded, and is bounded against its counterpart on purpose.**
OCCT's distance search needs a `TopoDS_Shape`, and a plane is not one. Bounding it to an
arbitrary patch would make the answer depend on the patch size — so the patch is derived
from the *other* operand's bounding box, which makes it exact rather than approximate:
every point of a shape lies inside its bounding box, so the foot of the perpendicular
from the closest point lies inside that box's projection onto the plane, and a patch
covering that projection therefore contains the true minimiser. The margin exists only so
a degenerate operand (a single point) still yields a face OCCT will accept; it cannot
change the number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Final

from app.catia.ops import vocabulary
from app.kernel.errors import GeometryError
from app.kernel.occt import classify
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.selectors import SUB_ENTITY_MARK, select_edges, select_faces
from app.kernel.occt.sketching import frame_of
from app.kernel.occt.topology import compound, edges, faces

#: Selector words that name **edges** rather than faces when written after a `#`.
#: Everything else in the vocabulary resolves faces — see the module docstring.
_EDGE_WORDS: Final[frozenset[str]] = frozenset(
    {"convex", "concave", "vertical", "horizontal"}
)

#: Fraction of the projected extent added around a bounded plane patch, plus a floor in
#: millimetres for the degenerate case. Neither affects the measured distance; both exist
#: so `BRepBuilderAPI_MakeFace` is never handed a zero-area patch.
_PATCH_MARGIN_FRACTION: Final = 0.1
_PATCH_MARGIN_MM: Final = 1.0

#: Below this, two directions are treated as the same line for the angle report. It is a
#: dot-product threshold on unit vectors, so it is an angle of about 0.0008°.
_PARALLEL_EPSILON: Final = 1e-10


@dataclass(frozen=True)
class Element:
    """One resolved operand of a measurement.

    `shape` is None only for a plane, which has no bounded geometry until it is measured
    against something — see `bounded_against`. Every other kind carries a real shape, so
    a caller that has already bounded its planes can treat them uniformly.
    """

    #: What the caller wrote, echoed back so a payload is readable without the request.
    reference: str

    #: `point` · `plane` · `axis_system` · `body` · `faces` · `edges`.
    kind: str

    shape: Any = None
    position: tuple[float, float, float] | None = None
    frame: Any = None

    #: How many sub-entities a selector resolved to. 1 for everything that is not a
    #: selector, which is what lets a caller say "the angle needs a single planar face".
    entity_count: int = 1

    #: Human-readable, for the payload: `"the top face of boss"` beats `"boss#top"`.
    description: str = ""

    #: Set when this element is a plane that has been bounded against a counterpart.
    _bounded: bool = field(default=False, repr=False)

    @property
    def is_plane(self) -> bool:
        return self.kind == "plane"

    def require_shape(self) -> Any:
        """The shape to measure, or a clear refusal for an unbounded plane."""
        if self.shape is None:  # pragma: no cover - callers bound planes first
            raise GeometryError(
                f"{self.reference} is a construction plane, which has no extent of its "
                "own. It has to be bounded against whatever it is being measured "
                "against before OCCT can search it."
            )
        return self.shape

    def direction(self) -> tuple[tuple[float, float, float], str] | None:
        """A reference direction and what it is, or None when the element has none.

        The second half of the pair is not decoration: the angle between two planes is
        the angle between their normals, and the angle between a plane and an edge is
        *not* the angle between the plane and the edge. Naming what was compared is how
        the report stays unambiguous instead of picking a convention silently.
        """
        if self.kind in {"plane", "axis_system"}:
            axis = self.frame.Direction()
            label = "plane normal" if self.kind == "plane" else "axis-system main axis"
            return ((axis.X(), axis.Y(), axis.Z()), label)

        if self.kind == "faces" and self.entity_count == 1:
            face = faces(self.shape)[0]
            if classify.face_surface_type(face) != "Plane":
                return None
            normal = classify.face_normal(face)
            return None if normal is None else (normal, "face normal")

        if self.kind == "edges" and self.entity_count == 1:
            edge = edges(self.shape)[0]
            if classify.edge_curve_type(edge) != "Line":
                return None
            adaptor = symbol("BRepAdaptor_Curve")(edge)
            axis = adaptor.Line().Direction()
            return ((axis.X(), axis.Y(), axis.Z()), "edge direction")

        return None

    def as_frame(self) -> Any:
        """This element as a `gp_Ax3` — a place and an orientation — or a refusal.

        A construction plane and an axis system carry one already. A **single planar
        face** earns one too, built at its parametric centre with its outward normal:
        that is what makes `mirror about slab#top` mean the same thing as `mirror about
        a plane through the top face`, which is how an engineer says it. Anything else
        has no single orientation and is refused rather than approximated.
        """
        if self.frame is not None:
            return self.frame

        if self.kind == "faces" and self.entity_count == 1:
            face = faces(self.shape)[0]
            if classify.face_surface_type(face) == "Plane":
                normal = classify.face_normal(face)
                centre = face_centre(face)
                if normal is not None:
                    return symbol("gp_Ax3")(
                        symbol("gp_Pnt")(*centre), symbol("gp_Dir")(*normal)
                    )

        raise GeometryError(
            f"{self.reference} is {self.description}, which has no single plane to work "
            "from. Name a construction plane, an origin plane (XY, YZ, ZX), or one "
            "planar face such as boss#top."
        )

    def bounded_against(self, other: Element) -> Element:
        """A plane, bounded so it covers everything `other` could be closest to.

        Returns self unchanged for anything that is not an unbounded plane, so a caller
        can apply it to both operands without branching.
        """
        if not self.is_plane or self._bounded:
            return self
        if other.is_plane:  # pragma: no cover - handled analytically before this
            raise GeometryError(
                "Two construction planes have no bounded extent to search between. "
                "Their distance is computed directly instead."
            )
        return Element(
            reference=self.reference,
            kind=self.kind,
            shape=_patch_covering(self.frame, other.require_shape()),
            frame=self.frame,
            description=self.description,
            _bounded=True,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reference": self.reference,
            "kind": self.kind,
            "description": self.description,
        }
        if self.position is not None:
            payload["position_mm"] = list(self.position)
        if self.kind in {"faces", "edges"}:
            payload["entity_count"] = self.entity_count
        return payload


def resolve_element(document: Any, reference: Any, *, tool: str) -> Element:
    """One element reference — a name, or `feature#selector` — to a measurable operand."""
    require()

    text = str(reference or "").strip()
    if not text:
        raise GeometryError(
            f"{tool} was given an empty element reference. Name a feature, a "
            f"construction plane, a point, or one entity of a feature as "
            f"feature{SUB_ENTITY_MARK}selector."
        )

    if SUB_ENTITY_MARK in text:
        return _resolve_sub_entity(document, text, tool=tool)

    matches = _namespaces_holding(document, text)
    if len(matches) > 1:
        found = ", ".join(sorted(matches))
        raise GeometryError(
            f"{text!r} names more than one thing in this part ({found}), so what to "
            f"measure is ambiguous. Rename one of them — {tool} will not guess."
        )
    if not matches:
        raise GeometryError(
            f"There is nothing called {text!r} in this part. {_known_names(document)}"
        )

    return _build(document, text, matches.pop())


def resolve_elements(document: Any, references: Any, *, tool: str) -> list[Element]:
    """Exactly two element references, resolved and mutually bounded.

    Bounding happens here rather than at each call site because it is pairwise: a plane
    is only boundable once its counterpart is known, and the counterpart may itself be
    the plane's reason for existing.
    """
    if not isinstance(references, (list, tuple)) or len(references) != 2:
        raise GeometryError(
            f"{tool} measures between exactly two elements. Name them as a list of two, "
            'for example ["bore", "outer_wall"].'
        )

    first = resolve_element(document, references[0], tool=tool)
    second = resolve_element(document, references[1], tool=tool)
    if first.is_plane and second.is_plane:
        return [first, second]
    return [first.bounded_against(second), second.bounded_against(first)]


def plane_frame(document: Any, reference: Any, *, tool: str) -> Any:
    """A named plane, as a frame — an origin plane, a constructed one, or a planar face.

    The one resolver every operation that takes "which plane" goes through: mirror,
    symmetry, scale, pattern direction, plane-offset. Before it, each had its own
    accept-list, and the plane-offset one still refused a planar face while blaming a
    phase that had already shipped.
    """
    require()

    text = str(reference or "").strip()
    if not text:
        raise GeometryError(f"{tool} needs a plane, and none was named.")

    if text.upper() in vocabulary.ORIGIN_PLANES:
        return frame_of(text.upper())

    return resolve_element(document, text, tool=tool).as_frame()


def axis_for(document: Any, reference: Any, *, tool: str) -> Any:
    """A named line, as a `gp_Ax1` — what a rotation or a circular pattern turns about.

    Four spellings, in the order an engineer reaches for them: a world axis (`X`, `Y`,
    `Z` — always present, so naming one needs no construction), an axis system's main
    axis, a single straight edge of the part (`boss#vertical`), and a construction
    plane's normal. The last is included because *"rotate about the plane's normal"* is
    how a plane gets used as an axis, and refusing it would send the design through a
    construction step that adds nothing.
    """
    require()

    text = str(reference or "").strip()
    if not text:
        raise GeometryError(f"{tool} needs an axis to turn about, and none was named.")

    if text.upper() in _WORLD_AXES:
        return symbol("gp_Ax1")(
            symbol("gp_Pnt")(0.0, 0.0, 0.0), symbol("gp_Dir")(*_WORLD_AXES[text.upper()])
        )

    element = resolve_element(document, text, tool=tool)
    found = element.direction()
    if found is None:
        raise GeometryError(
            f"{text} is {element.description}, which has no single direction to turn "
            f"about. Name a world axis (X, Y, Z), an axis system, a construction plane, "
            "or one straight edge."
        )
    direction, _ = found

    # An axis is a line, so it needs a point the line passes through — not just a
    # direction. A plane, point or axis system carries one; an edge's is its midpoint,
    # which is on the edge by construction. Defaulting to the origin instead would put
    # the rotation axis somewhere the design never mentioned.
    if element.position is not None:
        origin = element.position
    elif element.kind == "edges":
        origin = edge_midpoint(edges(element.require_shape())[0])
    else:  # pragma: no cover - every kind with a direction has one of the two above
        origin = (0.0, 0.0, 0.0)

    return symbol("gp_Ax1")(symbol("gp_Pnt")(*origin), symbol("gp_Dir")(*direction))


#: The world axes, accepted wherever a line is wanted. Mirrors `reference_ops._WORLD_AXES`
#: and exists separately only because that module is about *building* reference geometry
#: and this one is about *resolving* a name to something already there.
_WORLD_AXES: Final[dict[str, tuple[float, float, float]]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}


def edge_midpoint(edge: Any) -> tuple[float, float, float]:
    """A point the edge actually passes through, so the axis is a line and not a guess."""
    adaptor = symbol("BRepAdaptor_Curve")(edge)
    point = adaptor.Value((adaptor.FirstParameter() + adaptor.LastParameter()) / 2.0)
    return (point.X(), point.Y(), point.Z())


def face_centre(face: Any) -> tuple[float, float, float]:
    """The point at a face's parametric centre — where its own frame is anchored."""
    surface = symbol("BRepAdaptor_Surface")(face)
    point = surface.Value(
        (surface.FirstUParameter() + surface.LastUParameter()) / 2.0,
        (surface.FirstVParameter() + surface.LastVParameter()) / 2.0,
    )
    return (point.X(), point.Y(), point.Z())


def angle_between(first: Element, second: Element, *, tool: str) -> dict[str, Any]:
    """The angle between two elements that have a direction, folded to [0°, 90°].

    **Folded because the sense of a direction is not the caller's choice.** A plane's
    normal may point either way depending on how it was built, and a straight edge has no
    intrinsic start; reporting 170° for two walls a designer would call 10° apart is a
    number that is technically defensible and practically wrong every time.
    """
    left = first.direction()
    right = second.direction()
    missing = [
        element.reference
        for element, found in ((first, left), (second, right))
        if found is None
    ]
    if missing:
        raise GeometryError(
            f"{tool} cannot measure an angle to {', '.join(missing)}. An angle needs a "
            "direction, which a construction plane, a single planar face, a single "
            "straight edge and an axis system have — a whole body or a curved face does "
            "not. Measure the minimum distance instead, or name one flat face of it."
        )
    assert left is not None and right is not None  # noqa: S101 - narrowed by `missing`

    (left_axis, left_label), (right_axis, right_label) = left, right
    dot = abs(sum(a * b for a, b in zip(left_axis, right_axis, strict=True)))
    return {
        "angle_deg": math.degrees(math.acos(min(1.0, dot))),
        "angle_between": f"{left_label} to {right_label}",
        "reference_directions": [list(left_axis), list(right_axis)],
        "parallel": dot >= 1.0 - _PARALLEL_EPSILON,
    }


# -- resolution ---------------------------------------------------------------


def _resolve_sub_entity(document: Any, text: str, *, tool: str) -> Element:
    """`feature#selector` → the faces or edges it names, as one compound operand.

    The reference is handed to the selector layer **whole**, rather than parsed here and
    passed on as a predicate: `boss#vertical` has no predicate form (direction is
    measured from the edge's own curve), so a second parser here would work for four
    words and refuse the other two. One parser, one vocabulary.
    """
    feature, _, word = text.partition(SUB_ENTITY_MARK)
    feature, word = feature.strip(), word.strip().lower()

    shape = document.shape
    if shape is None:
        raise GeometryError(
            f"{tool} was asked for {text!r}, but nothing has been built in "
            f"{document.name} yet."
        )

    if word in _EDGE_WORDS:
        found = select_edges(shape, text, tool=tool, document=document)
        kind, noun = "edges", "edge"
    else:
        found = select_faces(shape, text, tool=tool, document=document)
        kind, noun = "faces", "face"

    if word == "all":
        description = f"every {noun} of {feature}"
    else:
        description = f"the {word} {noun if len(found) == 1 else noun + 's'} of {feature}"

    return Element(
        reference=text,
        kind=kind,
        shape=compound(found),
        entity_count=len(found),
        description=description,
    )


def _namespaces_holding(document: Any, name: str) -> set[str]:
    """Every namespace of the document that has something under this name."""
    checks = (
        ("point", document.has_point),
        ("plane", document.has_plane),
        ("axis system", document.has_axis_system),
        ("feature", document.has_feature),
    )
    return {label for label, holds in checks if holds(name)}


def _build(document: Any, name: str, namespace: str) -> Element:
    if namespace == "point":
        point = document.point(name)
        return Element(
            reference=name,
            kind="point",
            shape=symbol("BRepBuilderAPI_MakeVertex")(
                symbol("gp_Pnt")(*point.position)
            ).Vertex(),
            position=point.position,
            description=f"the reference point {name}",
        )

    if namespace == "plane":
        plane = document.plane(name)
        return Element(
            reference=name,
            kind="plane",
            frame=plane.frame,
            position=plane.origin_mm(),
            description=f"the construction plane {name}",
        )

    if namespace == "axis system":
        system = document.axis_system(name)
        location = system.frame.Location()
        return Element(
            reference=name,
            kind="axis_system",
            shape=symbol("BRepBuilderAPI_MakeVertex")(location).Vertex(),
            frame=system.frame,
            position=(location.X(), location.Y(), location.Z()),
            description=f"the origin of axis system {name}",
        )

    # **A feature's shape is the whole part as it stood after that feature**, not the
    # material that feature added — that is what `body()` means everywhere else, and
    # what a boolean's `tool_body` consumes. Measuring `boss` against `slab` therefore
    # compares two generations of the same part and reports their overlap as the whole
    # slab, which is arithmetically right and almost never the question. The description
    # says so in the payload rather than leaving it to be inferred from a surprising
    # number; `boss#all` is the spelling for the boss's own faces.
    return Element(
        reference=name,
        kind="body",
        shape=document.body(name),
        description=f"the part as it stood after {name}",
    )


def _known_names(document: Any) -> str:
    """What this part *does* contain, so a mistyped reference is one step from fixed.

    **The design's own names, not `feature_names()`.** That method returns CATIA-style
    names (`Pad.1`, `Pad.2`), which is what a feature listing shows and *not* what
    `document.feature()` resolves — a hint built from it would answer "there is nothing
    called `slab`" with a list of three names that also do not work.
    """
    groups = (
        ("features", [feature.name for feature in document]),
        ("planes", document.plane_names()),
        ("points", document.point_names()),
        ("axis systems", document.axis_system_names()),
    )
    listed = [f"{label}: {', '.join(names)}" for label, names in groups if names]
    return ("It holds " + "; ".join(listed) + ".") if listed else "It holds nothing yet."


# -- bounding a plane ---------------------------------------------------------


def _patch_covering(frame: Any, shape: Any) -> Any:
    """A planar face large enough that the distance to it equals the distance to the plane.

    See the module docstring for why this is exact. The eight bounding-box corners are
    projected into the plane's own frame and the patch spans their extent; a shape lies
    inside its box, projection is affine, so the foot of every perpendicular from the
    shape lands inside the patch.
    """
    box = symbol("Bnd_Box")()
    symbol("BRepBndLib").Add_s(shape, box, True)
    if box.IsVoid():  # pragma: no cover - an empty operand fails earlier
        raise GeometryError(
            "The shape a construction plane is being measured against is empty, so "
            "there is nothing to bound the plane around."
        )

    low_x, low_y, low_z, high_x, high_y, high_z = box.Get()
    origin = frame.Location()
    x_axis, y_axis = frame.XDirection(), frame.YDirection()

    us: list[float] = []
    vs: list[float] = []
    for corner_x in (low_x, high_x):
        for corner_y in (low_y, high_y):
            for corner_z in (low_z, high_z):
                offset = (
                    corner_x - origin.X(),
                    corner_y - origin.Y(),
                    corner_z - origin.Z(),
                )
                us.append(
                    offset[0] * x_axis.X() + offset[1] * x_axis.Y() + offset[2] * x_axis.Z()
                )
                vs.append(
                    offset[0] * y_axis.X() + offset[1] * y_axis.Y() + offset[2] * y_axis.Z()
                )

    u_margin = max(_PATCH_MARGIN_MM, (max(us) - min(us)) * _PATCH_MARGIN_FRACTION)
    v_margin = max(_PATCH_MARGIN_MM, (max(vs) - min(vs)) * _PATCH_MARGIN_FRACTION)
    return symbol("BRepBuilderAPI_MakeFace")(
        symbol("gp_Pln")(frame),
        min(us) - u_margin,
        max(us) + u_margin,
        min(vs) - v_margin,
        max(vs) + v_margin,
    ).Face()


__all__ = [
    "Element",
    "angle_between",
    "axis_for",
    "edge_midpoint",
    "face_centre",
    "plane_frame",
    "resolve_element",
    "resolve_elements",
]
