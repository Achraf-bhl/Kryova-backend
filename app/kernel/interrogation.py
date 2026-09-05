"""What an interrogation asks and what shape the answer has, above any one backend.

Master plan 3.2 and 3.3. `app.kernel.measurement` declares what a *measured part*
reports — mass, volume, bounds. This declares what an *interrogated* part reports:
wall thickness, draft relative to a pull direction, curvature, undercuts, and the
clearance or interference between two bodies. The split is real. A measurement is a
property of one shape and is always defined; an interrogation asks a question that has a
premise ("pulled along +Z", "against this other body") and can come back "not
applicable".

Backend-neutral for the same reason `measurement.py` is: OCCT answers these today, a
CATIA seat answers the same questions through `catia_analysis_part`, and an assertion
must not be able to tell which one it read.

**Every report carries the offending entities, not only the summary number.** A
correction loop given `minimum_wall_mm = 1.4` can tell that the part is too thin and
nothing else; given the sample point where 1.4 was found it can say *where*, and that is
the difference between a repair that is aimed and one that is guessed — the same
argument `AssertionResult.gap` makes one layer up.

**The summary scalars are the assertion surface.** `minimum_wall_mm`,
`minimum_draft_deg`, `undercut_face_count`, `minimum_clearance_mm` and
`interference_volume_mm3` are the paths a design writes assertions against, so they are
declared here as constants rather than spelled inline — a rename is then one deliberate
edit rather than a silent downgrade of every assertion that used the old word.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.kernel import provenance

#: Paths an assertion may read from an interrogation payload.
MINIMUM_WALL_MM: Final = "minimum_wall_mm"
MINIMUM_DRAFT_DEG: Final = "minimum_draft_deg"
UNDERCUT_FACE_COUNT: Final = "undercut_face_count"
MINIMUM_CONCAVE_RADIUS_MM: Final = "minimum_concave_radius_mm"
MINIMUM_DIHEDRAL_DEG: Final = "minimum_dihedral_deg"
MINIMUM_CLEARANCE_MM: Final = "minimum_clearance_mm"
INTERFERENCE_VOLUME_MM3: Final = "interference_volume_mm3"
ORIENTED_BOUNDING_BOX_MM: Final = "oriented_bounding_box_mm"

Point = tuple[float, float, float]


@dataclass(frozen=True)
class ThicknessSample:
    """One ray's worth of wall thickness: where it started and what it found."""

    point: Point
    thickness_mm: float

    #: Index of the face the ray left from, in the shape's face traversal order. Stable
    #: for a given shape (`topology.explore` returns map order), which is what lets a
    #: repair say "the boss at face 12" rather than quoting a bare coordinate.
    face_index: int


@dataclass(frozen=True)
class ThicknessReport:
    """How thin the part gets, and where.

    **Always approximated.** Thickness is found by casting rays from a finite set of
    points, so the answer is the thinnest wall *among those sampled* — an upper bound on
    the true minimum, never a proof. A thin spot between two sample points is missed, and
    no sampling density removes that. Reported through
    `app.kernel.provenance.APPROXIMATED` so it can never be read as exact.
    """

    samples: tuple[ThicknessSample, ...] = ()

    #: Rays that left the solid without coming back — an open shell, or a point where the
    #: geometry is locally not a wall at all. Counted rather than silently dropped: a scan
    #: that measured four points out of four thousand is not a scan of the part.
    misses: int = 0

    #: Points per face the scan was asked for, carried so the number can be judged.
    samples_per_face: int = 0

    @property
    def minimum_mm(self) -> float | None:
        if not self.samples:
            return None
        return min(sample.thickness_mm for sample in self.samples)

    @property
    def thinnest(self) -> ThicknessSample | None:
        if not self.samples:
            return None
        return min(self.samples, key=lambda sample: sample.thickness_mm)

    def method(self) -> str:
        return (
            f"ray cast inward from {self.samples_per_face} points per face "
            f"({len(self.samples)} hits, {self.misses} misses)"
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "thickness_samples": len(self.samples),
            "thickness_misses": self.misses,
        }
        minimum = self.minimum_mm
        if minimum is None:
            provenance.attach(
                payload,
                MINIMUM_WALL_MM,
                provenance.unavailable(
                    "no ray cast from a face found a wall on the far side. The shape "
                    "may be an open shell, or every sample may have left through an "
                    "opening — check that the solid is closed before reading a wall "
                    "thickness from it."
                ),
            )
            return payload
        payload[MINIMUM_WALL_MM] = minimum
        thinnest = self.thinnest
        if thinnest is not None:
            payload["thinnest_point_mm"] = list(thinnest.point)
            payload["thinnest_face_index"] = thinnest.face_index
        provenance.attach(
            payload, MINIMUM_WALL_MM, provenance.approximated(self.method())
        )
        return payload


@dataclass(frozen=True)
class DraftFace:
    """One face's draft angle relative to a pull direction."""

    face_index: int

    #: Signed, in degrees, in [-90, +90]. `asin(n · pull)`: +90 means the face looks
    #: straight along the pull (a top face, fully demouldable), 0 means it is parallel to
    #: the pull (a vertical wall, which will drag), and negative means it belongs to the
    #: other half of the mould. The magnitude is the draft; the sign is the half.
    #:
    #: On a curved face this is the **worst** draft found across its samples, because a
    #: cylinder aligned with the pull is fine on two sides and dragging on the other two,
    #: and the average of that is a number describing no part of the real face.
    draft_deg: float

    area_mm2: float

    #: True when the face is planar, where one normal describes the whole face exactly.
    #: False when the value came from sampling a curved surface.
    planar: bool = True

    @property
    def magnitude_deg(self) -> float:
        return abs(self.draft_deg)


@dataclass(frozen=True)
class DraftReport:
    """Draft across every face, against one pull direction.

    **The basis depends on the part, and the report works it out rather than declaring
    one.** A planar face has a single exact normal, so its draft is `MEASURED`
    arithmetic. A curved face does not, so its worst draft is found by sampling and is
    `APPROXIMATED`. A part made only of planes therefore gets an exact draft answer, and
    one with a fillet on it gets an honest approximation — the alternative, picking one
    basis for both, would either understate the planar case or overstate the curved one.

    **The minimum is over all evaluated faces including the flat top**, which reads
    oddly and is right: a top face perpendicular to the pull has 90° of draft, so it can
    never be the minimum and costs nothing to include, while excluding it would need a
    rule for "is this a wall" that no geometry supports.
    """

    pull_direction: Point
    faces: tuple[DraftFace, ...] = ()

    #: Faces whose normal could not be evaluated. Named because a report that quietly
    #: skipped them would claim to have checked the whole part.
    unevaluated: int = 0

    #: Below this magnitude a face is reported as needing draft.
    required_deg: float = 0.0

    #: Points per face used where sampling was needed.
    samples_per_face: int = 0

    @property
    def curved_faces(self) -> int:
        return sum(1 for face in self.faces if not face.planar)

    @property
    def minimum_deg(self) -> float | None:
        """The smallest draft magnitude on the part, or None if nothing was evaluated."""
        if not self.faces:
            return None
        return min(face.magnitude_deg for face in self.faces)

    @property
    def worst(self) -> DraftFace | None:
        if not self.faces:
            return None
        return min(self.faces, key=lambda face: face.magnitude_deg)

    @property
    def undrafted(self) -> tuple[DraftFace, ...]:
        """Faces below the required draft, worst first."""
        return tuple(
            sorted(
                (f for f in self.faces if f.magnitude_deg < self.required_deg),
                key=lambda f: f.magnitude_deg,
            )
        )

    def basis(self) -> provenance.Record:
        if self.curved_faces == 0:
            return provenance.measured(
                "exact surface normal per planar face, angle to the pull axis"
            )
        return provenance.approximated(
            f"exact normal on planar faces; worst of {self.samples_per_face} sampled "
            f"normals on each of {self.curved_faces} curved faces"
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "draft_faces_evaluated": len(self.faces),
            "draft_faces_unevaluated": self.unevaluated,
            "draft_faces_curved": self.curved_faces,
            "pull_direction": list(self.pull_direction),
        }
        minimum = self.minimum_deg
        if minimum is None:
            provenance.attach(
                payload,
                MINIMUM_DRAFT_DEG,
                provenance.unavailable(
                    "no face on this shape had an evaluable normal, so draft could not "
                    "be computed against any pull direction."
                ),
            )
            return payload
        payload[MINIMUM_DRAFT_DEG] = minimum
        payload["undrafted_face_count"] = len(self.undrafted)
        if self.undrafted:
            payload["undrafted_face_indices"] = [f.face_index for f in self.undrafted]
        worst = self.worst
        if worst is not None:
            payload["worst_draft_face_index"] = worst.face_index
        provenance.attach(payload, MINIMUM_DRAFT_DEG, self.basis())
        return payload


@dataclass(frozen=True)
class UndercutReport:
    """Faces that cannot be reached from either half of the mould.

    **Approximated, and for a sharper reason than thickness.** Demouldability is a
    visibility question, answered here by casting one ray along the pull direction and
    one against it from a point on each face. A face is undercut when both rays are
    blocked by the part's own material. Sampling one point per face is right for the
    common case — a face is usually wholly visible or wholly hidden — and wrong for a
    large face that is occluded over only part of its area. That limitation is the
    method's, and is stated rather than hidden.

    Distinct from insufficient draft: a face can have textbook draft and still be
    undercut, if something else on the part stands in front of it.
    """

    pull_direction: Point
    undercut_faces: tuple[int, ...] = ()
    tested: int = 0
    untested: int = 0

    @property
    def count(self) -> int:
        return len(self.undercut_faces)

    def method(self) -> str:
        return (
            f"visibility ray cast along ±pull from one point per face "
            f"({self.tested} tested, {self.untested} untestable)"
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            UNDERCUT_FACE_COUNT: self.count,
            "undercut_faces_tested": self.tested,
            "undercut_faces_untested": self.untested,
        }
        if self.undercut_faces:
            payload["undercut_face_indices"] = list(self.undercut_faces)
        provenance.attach(
            payload, UNDERCUT_FACE_COUNT, provenance.approximated(self.method())
        )
        return payload


@dataclass(frozen=True)
class CurvatureReport:
    """The tightest curvature on the part, which is what bounds the tool that can cut it.

    Concave curvature is reported separately from convex, and the separation is the whole
    value: an outside corner of radius 0.5 mm is a chamfer nobody minds, while an inside
    corner of radius 0.5 mm demands a 1 mm cutter and a conversation about cost. Only the
    concave minimum constrains manufacturing.
    """

    #: Smallest concave (internal) radius found, in mm. None when the part has no
    #: concave curvature — a convex blob genuinely has none, which is not a failure.
    minimum_concave_radius_mm: float | None = None

    minimum_convex_radius_mm: float | None = None

    #: Faces whose curvature OCCT declined to evaluate — a degenerate patch, a pole.
    unevaluated: int = 0

    #: Points per face the scan sampled.
    samples_per_face: int = 0

    def method(self) -> str:
        return (
            f"principal curvatures sampled at {self.samples_per_face} points per face"
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"curvature_faces_unevaluated": self.unevaluated}
        if self.minimum_convex_radius_mm is not None:
            payload["minimum_convex_radius_mm"] = self.minimum_convex_radius_mm
        if self.minimum_concave_radius_mm is None:
            provenance.attach(
                payload,
                MINIMUM_CONCAVE_RADIUS_MM,
                provenance.unavailable(
                    "no concave curvature was found on this shape. A part built only "
                    "from planes and convex surfaces has none, and that is an answer, "
                    "not a failure to measure."
                ),
            )
            return payload
        payload[MINIMUM_CONCAVE_RADIUS_MM] = self.minimum_concave_radius_mm
        provenance.attach(
            payload, MINIMUM_CONCAVE_RADIUS_MM, provenance.approximated(self.method())
        )
        return payload


@dataclass(frozen=True)
class ContinuityReport:
    """How the faces meet along each edge — sharp, tangent, or somewhere between.

    Two things a design cares about come out of the same measurement.

    **The sharpest dihedral** is a stress raiser and a machining constraint. An interior
    angle of 20° is a knife edge: it concentrates stress into a singularity that no mesh
    refinement converges on, and no cutter reaches the bottom of it. This is the number a
    structural assertion wants long before anyone runs a solver.

    **Tangent edges** are where a blend meets what it blends. A fillet that came out
    tangent on one side and sharp on the other is a defect visible in this count and
    invisible in mass, volume and bounding box — which is exactly the blind spot the
    master plan wants covered.

    Measured, not sampled: the dihedral comes from two exact surface normals at a point
    on the shared edge.
    """

    #: Interior angle between adjacent faces at each edge, in degrees. 180° is tangent
    #: (a smooth blend), 90° is a box corner, small values are knife edges.
    minimum_dihedral_deg: float | None = None

    tangent_edges: int = 0
    sharp_edges: int = 0

    #: Edges with fewer than two adjacent faces — a free boundary on an open shell.
    #: Not a defect on its own, but it means the count above is not the whole part.
    open_edges: int = 0

    #: Edges whose faces would not evaluate. Reported so the check is auditable.
    unevaluated: int = 0

    #: The edge index where the sharpest dihedral was found.
    sharpest_edge_index: int | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tangent_edge_count": self.tangent_edges,
            "sharp_edge_count": self.sharp_edges,
            "open_edge_count": self.open_edges,
            "continuity_edges_unevaluated": self.unevaluated,
        }
        if self.minimum_dihedral_deg is None:
            provenance.attach(
                payload,
                MINIMUM_DIHEDRAL_DEG,
                provenance.unavailable(
                    "no edge on this shape is shared by two evaluable faces, so there is "
                    "no dihedral angle to report. A single face or an open shell has "
                    "none."
                ),
            )
            return payload
        payload[MINIMUM_DIHEDRAL_DEG] = self.minimum_dihedral_deg
        if self.sharpest_edge_index is not None:
            payload["sharpest_edge_index"] = self.sharpest_edge_index
        provenance.attach(
            payload,
            MINIMUM_DIHEDRAL_DEG,
            provenance.measured("exact surface normals at the midpoint of each edge"),
        )
        return payload


@dataclass(frozen=True)
class ClearanceReport:
    """How close two bodies come, or how far they overlap.

    The two outcomes are mutually exclusive and both are reported, because a caller
    asking "do these clash?" and one asking "is there room for the spanner?" want
    opposite halves of the same computation and neither should have to run it twice.

    **Distance is measured; interference volume is measured too.** OCCT's
    `BRepExtrema_DistShapeShape` is an exact extremum search, not a sampled one, and the
    overlap is a real boolean intersection integrated for volume. Neither is a sampling
    approximation — which is worth stating, because clearance *sounds* like the sort of
    thing that would be approximated and here is not.

    **An overlap that is not a question is reported as unavailable, never as zero.** A
    construction plane has no volume, so "how much does the part overlap the datum plane"
    has no answer — and `0.0` would be read as "they do not clash", which is a claim
    nobody made. `interference_unavailable` carries the reason instead.
    """

    #: Minimum distance between the two shapes, in mm. Zero when they touch or overlap.
    distance_mm: float | None = None

    #: Volume common to both, in mm³. Greater than zero means they interfere.
    interference_mm3: float = 0.0

    #: The closest point on each shape — what a drawing or a viewer would annotate.
    closest_points: tuple[Point, Point] | None = None

    #: Why the query failed, when it did.
    failure: str = ""

    #: Why an overlap volume is not a meaningful question for this pair, when it is not.
    #: Set means `interference_mm3` is not reported at all rather than reported as zero.
    interference_unavailable: str = ""

    @property
    def interferes(self) -> bool:
        return self.interference_mm3 > 0.0 and not self.interference_unavailable

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}

        if self.failure or self.distance_mm is None:
            provenance.attach(
                payload,
                MINIMUM_CLEARANCE_MM,
                provenance.unavailable(
                    self.failure
                    or "the minimum-distance search did not converge on these two shapes."
                ),
            )
        else:
            payload[MINIMUM_CLEARANCE_MM] = self.distance_mm
            provenance.attach(
                payload,
                MINIMUM_CLEARANCE_MM,
                provenance.measured("BRepExtrema minimum-distance extremum search"),
            )
            if self.closest_points is not None:
                payload["closest_points_mm"] = [
                    list(self.closest_points[0]),
                    list(self.closest_points[1]),
                ]

        if self.interference_unavailable:
            provenance.attach(
                payload,
                INTERFERENCE_VOLUME_MM3,
                provenance.unavailable(self.interference_unavailable),
            )
            return payload

        payload[INTERFERENCE_VOLUME_MM3] = self.interference_mm3
        payload["interferes"] = self.interferes
        provenance.attach(
            payload,
            INTERFERENCE_VOLUME_MM3,
            provenance.measured("boolean common, volume integrated"),
        )
        return payload


@dataclass(frozen=True)
class ValidityReport:
    """Whether the shape is well-formed, and where it is not.

    Deliberately **not** a measured quantity with a provenance record: validity is a
    boolean about the model rather than a number about the part, and a design asserts on
    it through `is_valid`, which `read_measurement` reports as a 1 or 0 like any other
    numeric path. Wrapping it as a measurement would invite `is_valid >= 0.5`, which is
    nobody's idea of a readable assertion.
    """

    valid: bool
    #: Sub-shape kind ("face", "edge", …) → how many of that kind failed the check.
    #: Empty when the shape is valid, because the walk is skipped entirely then.
    invalid_by_kind: dict[str, int] = field(default_factory=dict)

    @property
    def invalid_total(self) -> int:
        return sum(self.invalid_by_kind.values())

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "is_valid": self.valid,
            "invalid_subshape_count": self.invalid_total,
        }
        if self.invalid_by_kind:
            payload["invalid_by_kind"] = dict(self.invalid_by_kind)
        return payload


@dataclass(frozen=True)
class OrientedBox:
    """The tightest box around a shape, at whatever angle that box wants to be.

    Distinct from the axis-aligned box in `metrology.bounding_box_mm`, and the difference
    is money: a bar lying diagonally has an axis-aligned box several times its own
    volume, so "does this fit in the stock?" and "what billet do I buy?" are answered
    wrongly by the AABB and rightly by this. The AABB stays because it is what a viewer
    needs and it is far cheaper.

    `size` is the full extent along each of the box's own axes, sorted descending, so two
    shapes that are the same block at different angles compare equal on it.
    """

    centre: Point
    #: Full lengths along the box's own axes, largest first.
    size: tuple[float, float, float]
    #: The box's axes, in the same order as `size`.
    axes: tuple[Point, Point, Point]
    #: True when every axis is parallel to a global one, so this box and the
    #: axis-aligned box are the same box. Computed from the axes, not taken from
    #: OCCT's `IsAABox()`, which reports construction rather than orientation.
    is_axis_aligned: bool = False

    @property
    def volume_mm3(self) -> float:
        return self.size[0] * self.size[1] * self.size[2]

    def to_payload(self) -> dict[str, Any]:
        return {
            "centre": list(self.centre),
            "size": list(self.size),
            "axes": [list(axis) for axis in self.axes],
            "volume_mm3": self.volume_mm3,
            "is_axis_aligned": self.is_axis_aligned,
        }


@dataclass
class InterrogationPayload:
    """Several interrogations merged into one payload an assertion suite can read.

    Assembled rather than returned wholesale because the questions are independent and
    expensive: a design that only asserts on wall thickness should not pay for a draft
    scan. `merge` keeps each report's provenance sidecar intact as it goes, which a plain
    `dict.update` would flatten and lose.
    """

    values: dict[str, Any] = field(default_factory=dict)

    def merge(self, payload: dict[str, Any]) -> InterrogationPayload:
        incoming_provenance = payload.get(provenance.PROVENANCE_KEY)
        for key, value in payload.items():
            if key == provenance.PROVENANCE_KEY:
                continue
            self.values[key] = value
        if isinstance(incoming_provenance, dict):
            sidecar = self.values.setdefault(provenance.PROVENANCE_KEY, {})
            sidecar.update(incoming_provenance)
        return self

    def add(self, report: Any) -> InterrogationPayload:
        return self.merge(report.to_payload())

    def as_dict(self) -> dict[str, Any]:
        return self.values


def unit_vector(direction: Sequence[float]) -> Point:
    """Normalise a direction, refusing a zero one rather than dividing by zero."""
    import math

    if len(direction) != 3:
        raise ValueError(
            f"A direction needs three components, got {len(direction)}. Pull directions "
            "and axes are 3D vectors, e.g. [0, 0, 1]."
        )
    x, y, z = (float(component) for component in direction)
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        raise ValueError(
            "A zero-length direction points nowhere, so draft and undercut have no "
            "meaning against it. Give the axis the mould opens along, e.g. [0, 0, 1]."
        )
    return (x / length, y / length, z / length)


__all__ = [
    "INTERFERENCE_VOLUME_MM3",
    "MINIMUM_CLEARANCE_MM",
    "MINIMUM_CONCAVE_RADIUS_MM",
    "MINIMUM_DIHEDRAL_DEG",
    "MINIMUM_DRAFT_DEG",
    "MINIMUM_WALL_MM",
    "ORIENTED_BOUNDING_BOX_MM",
    "UNDERCUT_FACE_COUNT",
    "ClearanceReport",
    "ContinuityReport",
    "CurvatureReport",
    "DraftFace",
    "DraftReport",
    "InterrogationPayload",
    "OrientedBox",
    "ThicknessReport",
    "ThicknessSample",
    "UndercutReport",
    "ValidityReport",
    "unit_vector",
]
