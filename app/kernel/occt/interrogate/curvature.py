"""Principal curvature, and the tool radius it implies — master plan 3.2.

The manufacturing question this answers: **what is the smallest cutter that can reach
every internal corner of this part?** An inside corner of radius R needs an end mill of
diameter 2R or smaller, and small cutters are slow, fragile and expensive. A part whose
tightest internal radius is 0.4 mm is not wrong, but it has quietly chosen a 0.8 mm
cutter and nobody was told.

**Concave and convex are reported separately, and only concave constrains anything.** An
external radius of 0.4 mm is a small round on a corner — every tool can produce it, it
costs nothing. The same number on an *internal* corner sets the machining strategy. A
single "minimum radius" merging both would be dominated by whichever happened to be
smaller and would mean nothing.

**Sign convention, measured rather than assumed.** OCCT's principal curvatures are
signed against the *surface* normal, and a face's outward normal is the opposite of that
whenever the face is REVERSED — so the raw number must be corrected before it means
anything. Both halves were checked against geometry whose answer is known:

| geometry | orientation | raw `MinCurvature` | corrected | is |
|---|---|---|---|---|
| solid cylinder Ø10, barrel | FORWARD | −0.2 | −0.2 | convex |
| Ø6 bore through a block | REVERSED | −0.3333 | +0.3333 | concave |

So against the outward normal, **negative is convex and positive is concave** — the
opposite of the intuition that a curve bending "towards" the normal should be positive,
and the reason this table is in the file rather than in a commit message. Both raw values
are exactly −1/R for their radius, which is what confirms the correction is a sign flip
and not a scale error.

**Approximated, and by the same argument as thickness.** Curvature is a point property
sampled on a grid, so the tightest sampled radius is an upper bound on the tightest real
one. Planes are skipped rather than sampled — a plane's curvature is exactly zero, its
radius is infinite, and running the evaluator over every flat face on a bracket to learn
that is pure cost.
"""

from __future__ import annotations

from typing import Any, Final

from app.kernel.interrogation import CurvatureReport
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.classify import face_surface_type
from app.kernel.occt.interrogate.sampling import DEFAULT_GRID, sample_face
from app.kernel.occt.topology import faces

#: Curvature below this is a flat spot: the radius it implies exceeds any real part, and
#: dividing by it produces a number that is arithmetically fine and physically absurd.
#: 1e-9 mm⁻¹ is a radius of 1,000 km.
_FLAT_CURVATURE: Final = 1e-9

#: Resolution for the local-property evaluator, matching the one used for normals.
_CURVATURE_TOLERANCE: Final = 1e-7


def scan_curvature(shape: Any, *, grid: int = DEFAULT_GRID) -> CurvatureReport:
    """The tightest concave and convex radii found on the shape.

    Both come back None on a part built only from planes, which is the correct answer for
    a machined block and is reported as `UNAVAILABLE` with that explanation rather than
    as an infinity or a zero.
    """
    require()

    tightest_concave: float | None = None
    tightest_convex: float | None = None
    unevaluated = 0

    for face in faces(shape):
        if face_surface_type(face) == "Plane":
            continue

        adaptor = symbol("BRepAdaptor_Surface")(face)
        reversed_face = str(face.Orientation()).rsplit(".", 1)[-1] == "TopAbs_REVERSED"

        evaluated_any = False
        for surface_point in sample_face(face, grid=grid):
            properties = symbol("BRepLProp_SLProps")(
                adaptor, surface_point.u, surface_point.v, 2, _CURVATURE_TOLERANCE
            )
            if not properties.IsCurvatureDefined():
                continue
            evaluated_any = True

            for curvature in (
                float(properties.MinCurvature()),
                float(properties.MaxCurvature()),
            ):
                # The surface's curvature is expressed against the *surface* normal; a
                # REVERSED face's outward normal is the opposite of it, which flips
                # concave and convex. Same correction as `face_normal_at`, for the same
                # reason, and forgetting it here reports every pocket as a boss.
                against_outward = -curvature if reversed_face else curvature
                if abs(against_outward) < _FLAT_CURVATURE:
                    continue
                radius = 1.0 / abs(against_outward)
                if against_outward > 0.0:
                    if tightest_concave is None or radius < tightest_concave:
                        tightest_concave = radius
                elif tightest_convex is None or radius < tightest_convex:
                    tightest_convex = radius

        if not evaluated_any:
            unevaluated += 1

    return CurvatureReport(
        minimum_concave_radius_mm=tightest_concave,
        minimum_convex_radius_mm=tightest_convex,
        unevaluated=unevaluated,
        samples_per_face=grid * grid,
    )


__all__ = ["scan_curvature"]
