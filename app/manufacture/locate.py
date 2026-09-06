"""Finding, in the solid, the feature a traced dimension belongs to.

A dimension needs two things: a number, and somewhere to draw it. `dimensions.py`
supplies the number from the design; this supplies the place, from the geometry.

**Only what can be located exactly is located.** A cylindrical face has a radius
and an axis, both exact, and if its axis points along the view direction it
appears in that view as a circle with a knowable centre. That is enough to put a
leader on it and be certain the leader points at the right thing. Nothing here
guesses: a dimension whose feature cannot be found this way is reported unplaced
rather than drawn somewhere plausible, because a dimension pointing at the wrong
feature is worse than a dimension that is missing — a missing one gets queried,
and a wrong one gets machined.

**Radius matching, and why it is safe here.** A traced dimension carries a number
and a symbol; the geometry carries cylindrical faces with radii. They are matched
by value, to a tolerance far tighter than any feature spacing (`MATCH_TOLERANCE_MM`,
one micron), and a `radius_mm = 5` fillet legitimately matches four faces — the
four corners of a plate — which becomes `4X R5`, the ISO 129-1 form. What is
*not* safe is two different design parameters that happen to hold the same
number: `corner_mm = 5` and `boss_r_mm = 5` are indistinguishable by value, and
this reports the collision rather than picking one. `find_circles` returns the
groups; `layout.py` decides, and refuses when two traced dimensions want the same
group.

**Dedupe by axis position, not by face.** OCCT splits a full bore into two
half-cylinder faces at the seam as often as not, so counting faces would report a
single hole as `2X`. Faces are grouped by (radius, projected centre) rounded to
the micron, which is a property of the hole rather than of how the kernel chose
to trim it.

This module imports `app.kernel` and `app.render` read-only, through their public
surfaces, and adds nothing to either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from app.render.views import View

#: How close two lengths must be to be the same length, in millimetres. Tight,
#: because these are exact kernel values on both sides — a design parameter and
#: an analytic surface's radius — not sampled ones. A loose tolerance here would
#: let a 5.00 mm fillet claim a 5.02 mm one's leader.
MATCH_TOLERANCE_MM: Final = 1e-6

#: How nearly parallel a cylinder's axis must be to the view direction for the
#: face to read as a circle in that view. 1e-9 on the dot product: an axis that
#: is a thousandth of a degree off is a draft angle, not a bore, and it does not
#: project to a circle.
PARALLEL_TOLERANCE: Final = 1e-9

#: How far a centre mark extends past the circle it marks, as a fraction of the
#: radius. ISO 128-23 wants the centre line to overrun the feature slightly so
#: the cross is visible against the outline.
CENTRE_MARK_OVERRUN: Final = 0.35


@dataclass(frozen=True)
class CircleGroup:
    """Every circular feature of one radius that reads as a circle in one view.

    `centres` is sorted, so the same part always yields the same anchor and the
    same DXF. Determinism is defended at each cheap place to lose it here for the
    same reason `app/render/` defends it: a drawing that differs between two runs
    of the same design cannot be diffed, and a diff is how a reviewer sees what
    an edit did.
    """

    view: str
    radius_mm: float
    centres: tuple[tuple[float, float], ...]

    @property
    def count(self) -> int:
        return len(self.centres)

    @property
    def anchor(self) -> tuple[float, float]:
        """Where the one leader for this group points. The first centre, sorted."""
        return self.centres[0]

    def centre_marks(self) -> tuple[tuple[tuple[float, float], ...], ...]:
        """A cross at each centre, in view millimetres, as two polylines each."""
        reach = self.radius_mm * (1.0 + CENTRE_MARK_OVERRUN)
        marks: list[tuple[tuple[float, float], ...]] = []
        for x, y in self.centres:
            marks.append(((x - reach, y), (x + reach, y)))
            marks.append(((x, y - reach), (x, y + reach)))
        return tuple(marks)


def find_circles(shape: Any, view: View) -> tuple[CircleGroup, ...]:
    """Every cylindrical feature that appears as a circle in `view`, grouped by radius.

    Returns groups sorted by radius, largest first — the order a drawing
    dimensions them in, outside feature before inside, and a stable one.

    An empty result is a true answer about a part with no round features in this
    view, not a failure. A caller with a radius dimension and no group to hang it
    on tables the dimension.
    """
    from app.kernel.occt import classify
    from app.kernel.occt.binding import symbol
    from app.kernel.occt.topology import FACE, explore

    direction = view.direction
    by_radius: dict[int, set[tuple[int, int]]] = {}
    radii: dict[int, float] = {}
    centres: dict[tuple[int, int], tuple[float, float]] = {}

    for found in explore(shape, FACE):
        # Face_s once: `explore` answers in base TopoDS_Shape and both `classify`
        # and the adaptor are overloaded on the concrete type (CLAUDE.md).
        face = symbol("TopoDS").Face_s(found)
        if classify.face_surface_type(face) != "Cylinder":
            continue
        cylinder = symbol("BRepAdaptor_Surface")(face).Cylinder()
        axis = cylinder.Axis()
        along = axis.Direction()
        dot = along.X() * direction[0] + along.Y() * direction[1] + along.Z() * direction[2]
        if abs(abs(dot) - 1.0) > PARALLEL_TOLERANCE:
            continue

        location = axis.Location()
        centre = view.to_view_mm(location.X(), location.Y(), location.Z())
        radius = float(cylinder.Radius())

        radius_key = _key(radius)
        centre_key = (_key(centre[0]), _key(centre[1]))
        radii.setdefault(radius_key, radius)
        centres.setdefault(centre_key, centre)
        by_radius.setdefault(radius_key, set()).add(centre_key)

    groups = [
        CircleGroup(
            view=view.name,
            radius_mm=radii[radius_key],
            centres=tuple(centres[key] for key in sorted(keys)),
        )
        for radius_key, keys in by_radius.items()
    ]
    groups.sort(key=lambda group: (-group.radius_mm, group.centres))
    return tuple(groups)


def _key(value: float) -> int:
    """A length as a whole number of nanometres, for grouping.

    Integer keys rather than rounded floats because two values a hair either side
    of a rounding boundary must land in the same bucket or not at all, and float
    keys make that depend on which of the two the dict saw first.
    """
    return int(round(value * 1e6))


def match_radius(
    groups: tuple[CircleGroup, ...], radius_mm: float
) -> tuple[CircleGroup | None, str | None]:
    """The one group of this radius, or `None` and a reason there is not one.

    Returns `(group, reason)`. `reason` is non-empty exactly when `group` is
    `None`, and it is written for the dimension table, so it says what the
    drawing could not do and what would fix it.

    Two groups can never share a radius — they are keyed on it — so the only
    failure is finding none, which happens for a real reason worth printing: the
    feature is not round, is not visible as a circle in this view, or was
    modelled at a size the design does not state.
    """
    for group in groups:
        if abs(group.radius_mm - radius_mm) <= MATCH_TOLERANCE_MM:
            return group, None
    if not groups:
        return None, "no round feature reads as a circle in this view"
    available = ", ".join(f"R{group.radius_mm:g}" for group in groups)
    return None, (
        f"no round feature of R{radius_mm:g} in this view; this view has {available}"
    )


__all__ = [
    "CENTRE_MARK_OVERRUN",
    "MATCH_TOLERANCE_MM",
    "PARALLEL_TOLERANCE",
    "CircleGroup",
    "find_circles",
    "match_radius",
]
