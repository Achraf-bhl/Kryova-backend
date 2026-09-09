"""Measuring an OCCT shape, in mm-N-MPa, with nothing converted.

Fills in the payload declared by `app.kernel.measurement`, which is the vocabulary
`app.design.assertions` reads and the two-backend conformance harness compares. This
module knows OCCT; that one knows what a measurement *is*.

**Units.** The project rule is mm-N-MPa and nothing converts. OCCT is unitless — it
computes in whatever the input was, and the input is millimetres — so volume comes back
in mm³ and area in mm². The single exception is mass, because density arrives as kg/m³
(the unit every material table uses); that conversion lives in
`app.kernel.measurement.mass_kg` and appears nowhere else.

**Provenance travels with every number.** Each path gets an
`app.kernel.provenance` record saying how it was arrived at — `measured` for the
integrations and the traversals, `approximated` for the oriented box (the box for
a given orientation is exact; the *orientation* is a search), `unavailable` with a
reason for a mass with no density. The scans in `app.kernel.interrogation` already
did this and the base payload did not, so until 2026-09-09 every requirement met
by an exactly integrated volume reported its evidence as `unrecorded` — a
coverage table that could not tell an integration from a payload that recorded
nothing, which is half of what master plan 11.4 means by *by what evidence*.

**Cost.** Each of volume, surface area and centre of mass is a `BRepGProp` integration
over the whole shape — the expensive part of measuring, and the reason `Detail` exists.
Volume properties are computed **once** and read three times where the naive version
would integrate three times for volume, centre of mass and inertia. On a plan of 10⁵
operations that difference is the run.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from app.kernel import interrogation, provenance
from app.kernel import measurement as spec
from app.kernel.errors import MeasurementError
from app.kernel.interrogation import OrientedBox
from app.kernel.measurement import Detail
from app.kernel.occt import topology
from app.kernel.occt.binding import require, symbol

logger = logging.getLogger(__name__)

#: How close an oriented box's axis must be to a global axis to be called aligned.
#: Loose enough to absorb the orientation search's own arithmetic, far tighter than any
#: rotation a part would actually carry.
_AXIS_TOLERANCE: Final = 1e-9


def _volume_properties(shape: Any) -> Any:
    """One `GProp_GProps` carrying volume, centre of mass and inertia together.

    OCCT computes all three in a single integration; asking for them separately runs it
    three times for the same answer.
    """
    require()
    props = symbol("GProp_GProps")()
    symbol("BRepGProp").VolumeProperties_s(shape, props)
    return props


def volume_mm3(shape: Any) -> float:
    """Enclosed volume. Meaningless on an open shell, so that is refused, not returned."""
    if not topology.has_solid(shape):
        raise MeasurementError(
            "This shape encloses no solid, so it has no volume. Close the surface "
            "before asking for volume or mass — an open shell would integrate to a "
            "number that looks like an answer."
        )
    return float(_volume_properties(shape).Mass())


def surface_area_mm2(shape: Any) -> float:
    require()
    props = symbol("GProp_GProps")()
    symbol("BRepGProp").SurfaceProperties_s(shape, props)
    return float(props.Mass())


def centre_of_mass_mm(shape: Any) -> tuple[float, float, float]:
    centre = _volume_properties(shape).CentreOfMass()
    return (centre.X(), centre.Y(), centre.Z())


def bounding_box_mm(shape: Any) -> dict[str, list[float]]:
    """Axis-aligned bounds as {min, max, size}.

    `SetGap(0)` asks for the tight box. OCCT's default is deliberately conservative,
    and a bounding box that quietly includes a tolerance margin makes an envelope
    assertion pass on a part that does not fit — the exact class of false green this
    codebase refuses elsewhere.
    """
    require()
    box = symbol("Bnd_Box")()
    box.SetGap(0.0)
    symbol("BRepBndLib").Add_s(shape, box, True)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return {
        "min": [xmin, ymin, zmin],
        "max": [xmax, ymax, zmax],
        "size": [xmax - xmin, ymax - ymin, zmax - zmin],
    }


def oriented_bounding_box(shape: Any) -> OrientedBox:
    """The tightest box around the shape, at whatever angle that box wants to be.

    `bounding_box_mm` answers "what axis-aligned volume does this occupy", which is what
    a viewer and a scene graph want. This answers "what stock do I cut it from", which is
    a different question with a very different answer: a 200 mm bar lying on the XY
    diagonal has an axis-aligned box roughly twice its own footprint, so buying billet
    from the AABB buys the wrong billet.

    `optimal=True` asks OCCT to search orientations rather than take the first fit. It
    costs more and is the only version worth having — an "oriented" box that is not
    actually tight is the AABB with extra steps.

    Sizes come back sorted descending so two placements of the same block compare equal,
    and the axes are permuted to match. OCCT reports **half**-sizes; these are doubled to
    full extents, matching `bounding_box_mm.size`, because two neighbouring functions
    that mean different things by "size" is a bug waiting to be written.
    """
    require()
    box = symbol("Bnd_OBB")()
    symbol("BRepBndLib").AddOBB_s(shape, box, True, True, True)

    centre = box.Center()
    axes = (box.XDirection(), box.YDirection(), box.ZDirection())
    half_sizes = (box.XHSize(), box.YHSize(), box.ZHSize())

    ordered = sorted(
        zip(half_sizes, axes, strict=True), key=lambda pair: pair[0], reverse=True
    )
    directions = tuple((axis.X(), axis.Y(), axis.Z()) for _, axis in ordered)
    return OrientedBox(
        centre=(centre.X(), centre.Y(), centre.Z()),
        size=(
            float(ordered[0][0]) * 2.0,
            float(ordered[1][0]) * 2.0,
            float(ordered[2][0]) * 2.0,
        ),
        axes=directions,  # type: ignore[arg-type]
        is_axis_aligned=all(_is_global_axis(axis) for axis in directions),
    )


def _is_global_axis(axis: tuple[float, float, float]) -> bool:
    """Is this unit vector parallel to X, Y or Z, in either direction?

    **Not `Bnd_OBB.IsAABox()`.** That reports how the box was *constructed*, not how it
    is oriented: a box built by the optimal-orientation search returns false even when
    the orientation it found is exactly axis-aligned, which is the common case for the
    common part. Measured here instead, so the field means what its name says.
    """
    return sum(1 for component in axis if abs(abs(component) - 1.0) <= _AXIS_TOLERANCE) == 1


def inertia_tensor_mm5(shape: Any) -> list[list[float]]:
    """Moments about the centre of mass, before density is applied.

    Density is deliberately not folded in: this is a property of the *shape*, and
    applying mass here would hide a second unit conversion inside a function whose name
    does not mention mass.
    """
    matrix = _volume_properties(shape).MatrixOfInertia()
    return [[float(matrix.Value(row, col)) for col in range(1, 4)] for row in range(1, 4)]


def _record(payload: dict[str, Any], path: str, record: Any) -> None:
    """Attach one provenance record, and never let doing so break a measurement.

    Provenance is a *description* of a number, so a fault while describing must
    not cost the number itself — the same reasoning `app/ai/vision.py` applies to
    its own check. In practice `attach` cannot fail on a dict this function
    controls; the guard is here because the alternative is a measurement path that
    can 500 for a reason nobody would guess from the traceback.
    """
    try:
        provenance.attach(payload, path, record)
    except Exception:  # noqa: BLE001 - see above
        logger.debug("Could not record provenance for %s", path, exc_info=True)


def measure(
    shape: Any,
    *,
    density_kg_m3: float | None = None,
    detail: Detail = Detail.FULL,
) -> dict[str, Any]:
    """The measurement payload for one shape, to the requested level of detail.

    `has_solid` is reported first and is load-bearing. A design whose last operation
    failed leaves a shape with no solid, and every downstream number would be zero —
    which is a plausible-looking answer. So it is never returned as one: the payload
    says there is no solid and *omits* what cannot be measured, which the assertion
    engine reads as UNMEASURED rather than as a pass.
    """
    require()
    payload: dict[str, Any] = {spec.HAS_SOLID: topology.has_solid(shape)}
    payload.update(topology.census(shape))
    for path in topology.census(shape):
        _record(payload, path, provenance.measured("TopExp traversal, de-duplicated"))

    if not detail.includes(Detail.BOUNDS):
        return payload
    payload[spec.BOUNDING_BOX_MM] = bounding_box_mm(shape)
    _record(payload, spec.BOUNDING_BOX_MM, provenance.measured("Bnd_Box over the B-rep"))

    if not detail.includes(Detail.FULL):
        return payload
    payload[spec.SURFACE_AREA_MM2] = surface_area_mm2(shape)
    _record(
        payload, spec.SURFACE_AREA_MM2, provenance.measured("BRepGProp surface integration")
    )

    if not payload[spec.HAS_SOLID]:
        # Area is defined on an open shell; volume, centre of mass and mass are not.
        return payload

    properties = _volume_properties(shape)
    volume = float(properties.Mass())
    centre = properties.CentreOfMass()
    payload[spec.VOLUME_MM3] = volume
    payload[spec.CENTRE_OF_MASS_MM] = [centre.X(), centre.Y(), centre.Z()]
    integration = provenance.measured("BRepGProp volume integration")
    _record(payload, spec.VOLUME_MM3, integration)
    _record(payload, spec.CENTRE_OF_MASS_MM, integration)

    if density_kg_m3 is None:
        # Never invent a density. A mass from a guessed density is precisely a number
        # that looks measured and is not.
        payload["mass_is_provisional"] = True
        _record(
            payload,
            spec.MASS_KG,
            provenance.unavailable("no density has been set on this part"),
        )
    else:
        payload["density_kg_m3"] = density_kg_m3
        payload[spec.MASS_KG] = spec.mass_kg(volume, density_kg_m3)
        _record(
            payload,
            spec.MASS_KG,
            provenance.measured(
                f"volume integration times the assigned density, {density_kg_m3:g} kg/m^3"
            ),
        )

    # The billet question, and it was documented in `contract.py` for months while
    # nothing emitted it. `oriented_bounding_box` existed, was exported, and had no
    # caller — so a requirement on `oriented_bounding_box_mm.size` was accepted at
    # construction (the vocabulary allows a documented path) and then verified
    # UNMEASURED for ever. That is exactly the "wish with a number on it" the
    # vocabulary refusal claims to prevent, arriving through the front door.
    #
    # At FULL rather than at a coarser level because it costs an orientation
    # search, and `Detail` exists for latency.
    oriented = oriented_bounding_box(shape)
    # `size` and `volume_mm3` only, because those are the two paths `contract.py`
    # documents. The box also knows its centre and its axes — which is what you
    # need to actually orient the stock — and emitting them turned
    # `test_every_quantity_a_scan_emits_is_documented` red immediately, which is
    # that guard doing its job on the same commit that fixed the converse.
    # Publishing them is a contract change and belongs with the `source` field
    # discussed in `tests/test_oriented_bounding_box.py`.
    payload[interrogation.ORIENTED_BOUNDING_BOX_MM] = {
        "size": list(oriented.size),
        "volume_mm3": oriented.size[0] * oriented.size[1] * oriented.size[2],
    }
    # `approximated`, not `measured`, and the distinction is the point: the box for
    # a *given* orientation is exact, and the orientation is the result of OCCT's
    # search. A billet bought from it is right to within how well that search did,
    # which is not something this build can bound — so a requirement met on it is
    # visibly not a requirement met on an integrated volume.
    _record(
        payload,
        interrogation.ORIENTED_BOUNDING_BOX_MM,
        provenance.approximated("Bnd_OBB orientation search (AddOBB, optimal)"),
    )

    if detail.includes(Detail.INERTIA):
        matrix = properties.MatrixOfInertia()
        payload[spec.INERTIA_TENSOR_MM5] = [
            [float(matrix.Value(row, col)) for col in range(1, 4)] for row in range(1, 4)
        ]

    return payload


def face_area_mm2(face: Any) -> float:
    """Area of a single face — what a named-face assertion checks."""
    return surface_area_mm2(face)


__all__ = [
    "bounding_box_mm",
    "centre_of_mass_mm",
    "face_area_mm2",
    "inertia_tensor_mm5",
    "measure",
    "oriented_bounding_box",
    "surface_area_mm2",
    "volume_mm3",
]
