"""The documented vocabulary of numbers a design may assert on — master plan 3.4.

`app.design.assertions` reads a measurement payload by *path*: `mass_kg`,
`bounding_box_mm.size[2]`, `minimum_wall_mm`. Paths rather than a closed enum, because
the payload is whatever the measuring backend returned and a closed enum would have to be
widened every time a tool learned to report something new. That flexibility has a cost,
and this module is the payment: **without a written contract, a path is only as stable as
whoever last edited the backend that emitted it**, and an assertion whose path quietly
stopped being produced degrades to `UNMEASURED` — honest, silent, and indistinguishable
from a claim nobody got round to measuring.

So every quantity a design is invited to assert on is declared here, once, with its unit,
its meaning and the version it appeared in. Three things follow:

* **A rename becomes a deliberate act.** The old spelling stays in the table marked
  superseded rather than vanishing, so an assertion written against it can be told what
  happened instead of failing to find anything.
* **A backend can be checked.** `undocumented_paths` names any key a payload carries that
  this contract does not describe — which is how a backend that invented its own spelling
  for wall thickness gets caught in a test rather than in a design review.
* **The agent can be told what is measurable** without reading six modules, which is what
  `catalogue()` is for.

**Backend-neutral by construction.** OCCT fills these in today; a CATIA seat fills the
same names from `catia_measure` and `catia_analysis_part`. The contract is the reason an
assertion cannot tell which one measured it, and therefore the reason the two-backend
conformance harness in `app.kernel.conformance` means anything.

**Versioning is additive.** A new quantity bumps the minor version. Removing or
redefining one is a major bump and needs the superseded entry left behind — this is a
promise made to designs stored on disk, which outlive any particular backend.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.kernel import interrogation, measurement, provenance

#: Bumped when a quantity is added (minor) or removed/redefined (major). Recorded into
#: provenance records so a stored result can be read back by a later version that knows
#: what changed.
CONTRACT_VERSION: Final = "1.3"

_INDEX_SUFFIX: Final = re.compile(r"\[\d+\]$")


@dataclass(frozen=True)
class Entry:
    """One quantity a design may assert on."""

    path: str
    unit: str
    summary: str

    #: What a well-behaved backend usually reports this as. Documentation, not
    #: enforcement — the payload's own provenance sidecar is the truth for a given run,
    #: because a quantity that is normally measured can be unavailable on a given part.
    typical_basis: provenance.Basis

    #: Contract version this path first appeared in.
    since: str = "1.0"

    #: Set when a path has been replaced, naming what replaced it. Kept in the table
    #: rather than deleted so an old design gets an explanation, not a silence.
    superseded_by: str = ""

    #: True when the path holds a list and is addressed with an index, e.g.
    #: `bounding_box_mm.size[2]`.
    indexed: bool = False


#: The vocabulary. Grouped by the module that produces it, in the order an engineer would
#: meet them: what the part *is*, then what it can be *made into*.
QUANTITIES: Final[tuple[Entry, ...]] = (
    # -- measurement.py: properties of the shape itself ----------------------
    Entry(
        path=measurement.MASS_KG,
        unit="kg",
        summary="Mass, from volume and the assigned material density. Absent when no "
        "density has been set — never guessed.",
        typical_basis=provenance.Basis.MEASURED,
    ),
    Entry(
        path=measurement.VOLUME_MM3,
        unit="mm³",
        summary="Enclosed volume. Absent on a shape with no solid, where it would "
        "integrate to a plausible-looking zero.",
        typical_basis=provenance.Basis.MEASURED,
    ),
    Entry(
        path=measurement.SURFACE_AREA_MM2,
        unit="mm²",
        summary="Total area of every face, including internal ones.",
        typical_basis=provenance.Basis.MEASURED,
    ),
    Entry(
        path=f"{measurement.BOUNDING_BOX_MM}.size",
        unit="mm",
        summary="Axis-aligned extent as [x, y, z]. What a viewer needs; not what stock "
        "to buy — see oriented_bounding_box_mm.size for that.",
        typical_basis=provenance.Basis.MEASURED,
        indexed=True,
    ),
    Entry(
        path=f"{measurement.BOUNDING_BOX_MM}.min",
        unit="mm",
        summary="Lower corner of the axis-aligned box, as [x, y, z].",
        typical_basis=provenance.Basis.MEASURED,
        indexed=True,
    ),
    Entry(
        path=f"{measurement.BOUNDING_BOX_MM}.max",
        unit="mm",
        summary="Upper corner of the axis-aligned box, as [x, y, z].",
        typical_basis=provenance.Basis.MEASURED,
        indexed=True,
    ),
    Entry(
        path=measurement.CENTRE_OF_MASS_MM,
        unit="mm",
        summary="Centroid of the solid, as [x, y, z]. Uniform density assumed.",
        typical_basis=provenance.Basis.MEASURED,
        indexed=True,
    ),
    Entry(
        path="face_count",
        unit="count",
        summary="Distinct faces. De-duplicated — see topology.explore on why that is "
        "not the same as what a traversal reports.",
        typical_basis=provenance.Basis.MEASURED,
    ),
    Entry(
        path="edge_count",
        unit="count",
        summary="Distinct edges, de-duplicated.",
        typical_basis=provenance.Basis.MEASURED,
    ),
    Entry(
        path="solid_count",
        unit="count",
        summary="Enclosed solids. More than one means the operation left the part in "
        "pieces, which is usually a defect and never a warning on its own.",
        typical_basis=provenance.Basis.MEASURED,
    ),
    # -- metrology.py: the oriented box (3.1) --------------------------------
    Entry(
        path=f"{interrogation.ORIENTED_BOUNDING_BOX_MM}.size",
        unit="mm",
        summary="Tightest box at any orientation, largest dimension first. The billet "
        "question: a bar lying diagonally has an axis-aligned box far larger than "
        "itself.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
        indexed=True,
    ),
    Entry(
        path=f"{interrogation.ORIENTED_BOUNDING_BOX_MM}.volume_mm3",
        unit="mm³",
        summary="Volume of the oriented box — stock consumed, not part volume.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    # -- interrogation: what it can be made into (3.2, 3.3) ------------------
    Entry(
        path=interrogation.MINIMUM_WALL_MM,
        unit="mm",
        summary="Thinnest wall found by ray casting. An UPPER BOUND on the true "
        "minimum — a thin spot between samples is not found.",
        typical_basis=provenance.Basis.APPROXIMATED,
        since="1.1",
    ),
    Entry(
        path=interrogation.MINIMUM_DRAFT_DEG,
        unit="degrees",
        summary="Smallest draft magnitude against the stated pull direction. Zero means "
        "a wall parallel to the pull, which will drag on the tool.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path=interrogation.UNDERCUT_FACE_COUNT,
        unit="count",
        summary="Faces reachable from neither half of a straight two-part tool. Not the "
        "same as insufficient draft — an undercut needs a side-action, not a tilt.",
        typical_basis=provenance.Basis.APPROXIMATED,
        since="1.1",
    ),
    Entry(
        path=interrogation.MINIMUM_CONCAVE_RADIUS_MM,
        unit="mm",
        summary="Tightest internal radius, which bounds the cutter that can reach it. "
        "Absent on a part with no concave curvature, which is an answer.",
        typical_basis=provenance.Basis.APPROXIMATED,
        since="1.1",
    ),
    Entry(
        path=interrogation.MINIMUM_DIHEDRAL_DEG,
        unit="degrees",
        summary="Sharpest join between adjacent faces. 180° is a tangent blend, 90° a "
        "box corner; small values are knife edges and stress singularities.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="density_kg_m3",
        unit="kg/m³",
        summary="The material density mass was computed from. Echoed into the payload so "
        "a mass can never be read without the assumption behind it.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="pull_direction",
        unit="unit vector",
        summary="The direction draft and undercuts were analysed against, as [x, y, z]. "
        "The premise of both answers — neither means anything without it.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
        indexed=True,
    ),
    Entry(
        path="undrafted_face_count",
        unit="count",
        summary="Faces below the stated minimum draft. Zero is the assertable condition "
        "for a mouldable part.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="thinnest_point_mm",
        unit="mm",
        summary="Where the thinnest sampled wall was found, as [x, y, z]. A location, so "
        "a repair can be aimed rather than guessed.",
        typical_basis=provenance.Basis.APPROXIMATED,
        since="1.1",
        indexed=True,
    ),
    Entry(
        path="sharp_edge_count",
        unit="count",
        summary="Edges where adjacent faces meet at an angle rather than continuing "
        "smoothly.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="tangent_edge_count",
        unit="count",
        summary="Edges where adjacent faces meet smoothly — the signature of a blend "
        "that took on both sides.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="open_edge_count",
        unit="count",
        summary="Edges bounded by fewer than two faces. Non-zero on a solid means the "
        "shape is not closed, whatever its volume says.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="is_valid",
        unit="boolean",
        summary="Whether OCCT's own consistency check passes. Read as 1 or 0 by an "
        "assertion; false means the B-rep is malformed regardless of how it measures.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="invalid_subshape_count",
        unit="count",
        summary="How many sub-shapes failed the consistency check. Zero on a valid part.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path="interferes",
        unit="boolean",
        summary="Whether two shapes overlap at all. The boolean companion to "
        "interference_volume_mm3.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path=interrogation.MINIMUM_CLEARANCE_MM,
        unit="mm",
        summary="Exact minimum distance between two shapes. Zero when they touch OR "
        "overlap — read interference_volume_mm3 to tell those apart.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    Entry(
        path=interrogation.INTERFERENCE_VOLUME_MM3,
        unit="mm³",
        summary="Volume common to two shapes. Greater than zero is a clash. Unavailable "
        "when either element is a construction plane, which bounds no volume — never "
        "reported as a zero that would read as 'they do not clash'.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.1",
    ),
    # -- elements.py: one element, and pairs of them (3.3) --------------------
    Entry(
        path="angle_deg",
        unit="degrees",
        summary="Angle between two elements' reference directions, folded to [0°, 90°] "
        "because the sense of a plane normal or an edge is not the caller's choice. "
        "Read angle_between for which two directions were compared.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.2",
    ),
    Entry(
        path="length_mm",
        unit="mm",
        summary="Length of a measured edge, or the total over a selector that matched "
        "several — element.entity_count says how many.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.2",
    ),
    Entry(
        path="area_mm2",
        unit="mm²",
        summary="Area of a measured face, or the total over a selector that matched "
        "several. Not the same as surface_area_mm2, which is the whole part.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.2",
    ),
    Entry(
        path="diameter_mm",
        unit="mm",
        summary="Diameter of a cylindrical face or a circular edge — the bore question. "
        "Absent when the element is neither, which is an answer rather than a zero.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.2",
    ),
    Entry(
        path="radius_mm",
        unit="mm",
        summary="Radius of the same element diameter_mm describes. Both are reported "
        "because a drawing calls out one and a fillet the other.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.2",
    ),
    Entry(
        path="position_mm",
        unit="mm",
        summary="Where a measured point, axis system or construction plane sits, as "
        "[x, y, z].",
        typical_basis=provenance.Basis.MEASURED,
        since="1.2",
        indexed=True,
    ),
    Entry(
        path="normal",
        unit="unit vector",
        summary="Outward normal of a measured planar face, or the normal of a "
        "construction plane, as [x, y, z].",
        typical_basis=provenance.Basis.MEASURED,
        since="1.2",
        indexed=True,
    ),
    Entry(
        path="thread.nominal_diameter_mm",
        unit="mm",
        summary="Nominal diameter the thread designation names — the 10 in M10. Absent "
        "when the designation is not one this build reads, which is stated in words "
        "rather than guessed.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.3",
    ),
    Entry(
        path="thread.pitch_mm",
        unit="mm",
        summary="Thread pitch: written into the designation, given explicitly, or taken "
        "from the ISO 261 coarse series. Absent when none of the three applies.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.3",
    ),
    Entry(
        path="thread.minor_diameter_mm",
        unit="mm",
        summary="Tapping-drill diameter implied by the designation — what an internal "
        "thread is cut into. Derived from the pitch, so absent whenever pitch_mm is.",
        typical_basis=provenance.Basis.APPROXIMATED,
        since="1.3",
    ),
    Entry(
        path="thread.face_diameter_mm",
        unit="mm",
        summary="Measured diameter of the cylinder the thread was declared on — the "
        "number the designation was checked against.",
        typical_basis=provenance.Basis.MEASURED,
        since="1.3",
    ),
)

_BY_PATH: Final[dict[str, Entry]] = {item.path: item for item in QUANTITIES}

if len(_BY_PATH) != len(QUANTITIES):  # pragma: no cover - a typo in this file
    _duplicates = sorted(
        path for path, seen in Counter(item.path for item in QUANTITIES).items() if seen > 1
    )
    raise ValueError(
        f"The measurement contract declares {_duplicates} more than once. Each path must "
        "appear exactly once, or which definition applies depends on table order."
    )

#: The version must be at least as new as the newest entry, checked at import.
#:
#: It had already drifted once — entries declared `since="1.2"` while the constant still
#: said `1.1`, so a provenance record would have named a contract version in which four
#: of the quantities it carried did not exist. A version nobody bumps is worse than no
#: version, because it is believed.
_NEWEST_SINCE: Final = max(item.since for item in QUANTITIES)

if _NEWEST_SINCE > CONTRACT_VERSION:  # pragma: no cover - a missed bump in this file
    raise ValueError(
        f"The measurement contract declares quantities added in {_NEWEST_SINCE} while "
        f"CONTRACT_VERSION is still {CONTRACT_VERSION}. Bump it: the version travels "
        "into provenance records and is how a stored result is read back later."
    )


def entry(path: str) -> Entry | None:
    """The contract entry for a path, index suffix and all, or None if undocumented."""
    return _BY_PATH.get(normalise(path))


def normalise(path: str) -> str:
    """Strip an index suffix so `bounding_box_mm.size[2]` finds `bounding_box_mm.size`.

    An assertion addresses one component of a vector; the contract documents the vector.
    Declaring three entries per vector would triple the table and say nothing extra.
    """
    return _INDEX_SUFFIX.sub("", path)



def describe(path: str) -> str:
    """A one-line description of a path, for an error message or the agent's brief."""
    found = entry(path)
    if found is None:
        return f"{path} is not a documented measurement."
    if found.superseded_by:
        return (
            f"{found.path} ({found.unit}) — superseded by {found.superseded_by}. "
            f"{found.summary}"
        )
    return f"{found.path} ({found.unit}) — {found.summary}"


def catalogue() -> tuple[str, ...]:
    """Every current path with its unit and meaning, for documentation and prompts."""
    return tuple(
        describe(item.path) for item in QUANTITIES if not item.superseded_by
    )


def undocumented_paths(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Numeric paths a payload carries that this contract does not describe.

    The check that keeps the contract honest: a backend that invents a spelling, or a new
    quantity added to a payload but never written down, shows up here. Intended to be
    asserted empty by the kernel tests, which is what makes the table above a contract
    rather than a comment.

    Diagnostic and per-shape counters are excluded — `thickness_samples`,
    `draft_faces_curved` and their kin describe *how the scan went*, not the part, and
    nobody writes a design assertion against them.
    """
    from app.design.assertions import measurable_paths

    found: list[str] = []
    for path in measurable_paths(payload):
        normalised = normalise(path)
        if normalised in _BY_PATH or _is_diagnostic(normalised):
            continue
        found.append(normalised)
    return tuple(sorted(set(found)))


#: Suffixes and prefixes marking a payload key as scan diagnostics rather than a property
#: of the part. Kept as a rule instead of a list so a new scan's counters do not each need
#: a contract entry to avoid being reported as undocumented.
_DIAGNOSTIC_MARKERS: Final = (
    "_count_tested",
    "_samples",
    "_misses",
    "_evaluated",
    "_unevaluated",
    "_tested",
    "_untested",
    "_index",
    "_indices",
)


#: Payload blocks that describe *what was measured* rather than a property of the part.
#: `element.entity_count` says a selector matched two faces; it is part of the question,
#: not part of the answer, and nobody writes a design assertion against it.
_QUESTION_BLOCKS: Final = ("element.", "elements.", "elements[")


def _is_diagnostic(path: str) -> bool:
    if path.startswith(("thickness_", "draft_", "curvature_", "continuity_", "undercut_")):
        return True
    if path.startswith(_QUESTION_BLOCKS):
        return True
    return any(path.endswith(marker) for marker in _DIAGNOSTIC_MARKERS)


__all__ = [
    "CONTRACT_VERSION",
    "QUANTITIES",
    "Entry",
    "catalogue",
    "describe",
    "entry",
    "normalise",
    "undocumented_paths",
]
