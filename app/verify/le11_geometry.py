"""NAFEMS LE11 — "Solid cylinder/taper/sphere, temperature loading": **the solid only**.

A hollow body of revolution modelled as a 90 deg sector. Read from the base upward its
meridian is a spherical band, then a taper, then a straight cylinder; the inner boundary
is a sphere then a cylinder, the outer boundary a larger sphere, then the taper, then a
cylinder. The name "solid cylinder" distinguishes it from a *shell* benchmark, not from a
hollow one — it is bored out along its axis and `le11_solid()` refuses to be mistaken for
a lump: `tests/test_le11_geometry.py` measures the void.

Everything here is read off `docs/nafems-le11-geometry.md`, which is the sourcing record:
its section 1 carries the dimensioned NAFEMS figure reproduced by ESRD, cross-checked
against FeenoX's committed Gmsh input and FeaTool's script. No number in this module was
recalled. The one value the sources disagree on — the height of the spherical band — is
marked INFERRED below with the reason, exactly as section 6 settles it.

    THE UNIT TRAP. **Every source states LE11 in metres. This codebase is mm-N-MPa and
    converts nothing, so every length below is the sourced metre figure multiplied by
    1000, once, here.** That is the easy half. The dangerous half is that the benchmark's
    *load* is a temperature field written in the same metres:

        source, in metres:      dtheta = sqrt(x**2 + y**2) + z
        this model, in mm:      dtheta = (sqrt(x**2 + y**2) + z) / 1000

    A later lane that takes the published formula unchanged and evaluates it on the
    millimetre coordinates of this solid gets temperatures 1000x too large, therefore
    thermal strains 1000x too large, therefore stresses 1000x too large — and **nothing
    anywhere raises**. The run completes, the field looks like a smooth gradient, the
    contour plot is the right shape, and the answer at point A comes back near -105 000
    MPa instead of -105 MPa. There is no unit system in this repository to catch it. The
    division by 1000 is arithmetic on the cited formula (sourcing record section 2), not
    a new source. Carry this warning forward into whatever consumes `le11_solid()`.

Scope: this module builds the shape and states its dimensions. The material, the
temperature field, the four boundary conditions and the -105 MPa target live in the
sourcing record and are encoded by the lane that owns `app/verify/nafems.py`.
"""

from __future__ import annotations

import math
from typing import Any, Final

# -- Dimensions, in millimetres ----------------------------------------------
#
# Provenance per value; "figure" is the ESRD reproduction of the original NAFEMS
# dimensioned figure (sourcing record 1.1), "FeenoX" is `examples/nafems-le11.geo`
# (1.2), "FeaTool" is the second cross-check (1.3). Sourced values are given as the
# metre figure times 1000 so the conversion is visible at every line.

#: Inner spherical surface, radius arrow from the origin on the figure: 1.0 m.
#: Corroborated by FeenoX `Circle(1)` through {1.000, 0, 0} and FeaTool's circle
#: of radius 1. Point A sits on this sphere where it meets the base plane.
LE11_INNER_SPHERE_RADIUS_MM: Final = 1.0 * 1000.0

#: Outer spherical surface, radius arrow from the origin on the figure: 1.4 m.
#: FeenoX `Circle(2)` through {1.400, 0, 0}; FeaTool circle radius 1.4. It meets
#: the base plane at the outer edge of the base annulus, which is why the figure's
#: two base dimensions 1.0 + 0.4 add to exactly this.
LE11_OUTER_SPHERE_RADIUS_MM: Final = 1.4 * 1000.0

#: Inner cylindrical surface: the figure's `0.7071 m`, axis to inner cylinder.
#: Written as 1000 * sin 45 deg rather than as 707.1 because the figure's own
#: `45 deg` annotation on a sphere of radius 1.0 m *makes* it exact — the printed
#: 0.7071 is that number to four decimals. FeenoX writes the same thing as
#: `1.000*Sin(Pi/4)`. Using 707.1 flat would leave the junction 6.8 um off the
#: sphere it is supposed to lie on and hand OCCT a wire that does not close.
LE11_INNER_CYLINDER_RADIUS_MM: Final = LE11_INNER_SPHERE_RADIUS_MM * math.sqrt(0.5)

#: Outer cylindrical surface: the figure dimensions it in two hops from the axis,
#: `0.7071 m` + `0.2929 m` = 1.0 m exactly. FeenoX `Point(7)`/`Point(9)` x = 1.000;
#: FeaTool polygon x = 1. Note it is *equal to the inner sphere radius* and that is
#: a coincidence of the numbers, not a construction — do not fuse the two constants.
LE11_OUTER_CYLINDER_RADIUS_MM: Final = 1.0 * 1000.0

#: Axial height of the spherical band, base plane to the sphere/cylinder junction.
#:
#: **INFERRED.** The figure prints `0.700 m` and FeenoX builds `1.000*Sin(Pi/4)` =
#: 0.707107 m; the value taken here is FeenoX's, and the reason is the figure's own
#: other annotations rather than a preference between documents. A point at radius
#: 0.7071 m on a sphere of radius 1.0 m is at height sqrt(1.0**2 - 0.7071**2) =
#: 0.707107 m and cannot be at 0.700 m: read literally, the printed 0.700 puts the
#: junction 5.0 mm off the sphere the same figure says it lies on, and the profile
#: will not close. The `0.700` is a rounded annotation. FeaTool is not an
#: independent third vote — its 1.2124 m taper radius is sqrt(1.4**2 - 0.7**2), the
#: printed value carried through. Consequence of the choice: 7.1 mm in 1797 mm
#: (0.4%) of total height, and it does not move point A, which is on the base plane.
LE11_SPHERICAL_BAND_HEIGHT_MM: Final = LE11_INNER_SPHERE_RADIUS_MM * math.sqrt(0.5)

#: Axial height of the taper. The figure dimensions it as two bands of `0.345 m`;
#: there is no geometric feature on the line between them — the outer profile is one
#: straight segment across both, and none of the three sources puts a slope change
#: there. It is a mesh partition line in the original figure, so it is not modelled.
LE11_TAPER_HEIGHT_MM: Final = (0.345 + 0.345) * 1000.0

#: Axial height of the straight cylinder: figure `0.400 m`; FeenoX `.400`;
#: FeaTool 1.79 - 1.39.
LE11_CYLINDER_HEIGHT_MM: Final = 0.400 * 1000.0

#: Outer radius where the outer sphere hands over to the taper (point D), derived:
#: the point at the band height on the outer sphere, sqrt(R_out**2 - h_band**2).
#: FeenoX `Point(5)` builds exactly this expression and reaches 1.208305 m.
#: The taper is **not** tangent to the outer sphere here — the perpendicular
#: distance from the origin to the line D-F is 1.361 m, not 1.400 m — so D is a
#: genuine slope discontinuity. Do not "improve" the model by filleting it or by
#: making the taper tangent; that is a different body.
LE11_TAPER_BOTTOM_RADIUS_MM: Final = math.sqrt(
    LE11_OUTER_SPHERE_RADIUS_MM**2 - LE11_SPHERICAL_BAND_HEIGHT_MM**2
)

#: Overall axial extent, base plane to top annulus. Derived from the three heights.
LE11_TOTAL_HEIGHT_MM: Final = (
    LE11_SPHERICAL_BAND_HEIGHT_MM + LE11_TAPER_HEIGHT_MM + LE11_CYLINDER_HEIGHT_MM
)

#: The sector angle. `90 degrees` on the ESRD 3-D view, `Extrude {... Pi/2}` in
#: FeenoX, `90` in FeaTool. The two cut planes are the model's symmetry planes, so
#: the sector angle is not a modelling convenience — build 180 deg and the symmetry
#: restraints hold the wrong faces.
LE11_SECTOR_ANGLE_RAD: Final = math.pi / 2.0

#: Point A, the lower inside corner, in **model coordinates (x, y, z) in mm**. The
#: benchmark's target — direct axial stress, -105 MPa — is read here. The figure
#: dimensions it as `1.0 m` from the axis on the base plane; FeenoX names
#: `Point(2) = {1.000, 0, 0}` as A in a comment. It is a corner of the domain (inner
#: sphere meets base plane meets the y = 0 symmetry plane), so a mesher puts a node
#: exactly on it and a reader finds it without interpolating.
LE11_POINT_A: Final = (LE11_INNER_SPHERE_RADIUS_MM, 0.0, 0.0)

#: The meridian, as `(name, r, z)` in mm, in the order the profile is traversed from
#: A. This is the sourcing record's section 1.4 table, and it is exported because a
#: later lane needs to place selectors and probes on it. Letters are FeenoX's.
LE11_MERIDIAN_MM: Final[tuple[tuple[str, float, float], ...]] = (
    ("A", LE11_INNER_SPHERE_RADIUS_MM, 0.0),
    ("C", LE11_INNER_CYLINDER_RADIUS_MM, LE11_SPHERICAL_BAND_HEIGHT_MM),
    ("G", LE11_INNER_CYLINDER_RADIUS_MM, LE11_TOTAL_HEIGHT_MM),
    ("H", LE11_OUTER_CYLINDER_RADIUS_MM, LE11_TOTAL_HEIGHT_MM),
    ("F", LE11_OUTER_CYLINDER_RADIUS_MM, LE11_SPHERICAL_BAND_HEIGHT_MM + LE11_TAPER_HEIGHT_MM),
    ("D", LE11_TAPER_BOTTOM_RADIUS_MM, LE11_SPHERICAL_BAND_HEIGHT_MM),
    ("B", LE11_OUTER_SPHERE_RADIUS_MM, 0.0),
)

#: Exact volume of the 90 deg sector, in mm**3. **Derived analytically from the
#: dimensions above, not measured off the built shape** — the derivation is three
#: solids of revolution, each integrated in closed form and then quartered:
#:
#: 1. *the spherical band*, whose annular cross-section is
#:    pi*((R_out**2 - z**2) - (R_in**2 - z**2)) = pi*(R_out**2 - R_in**2) — constant
#:    in z, because both bounding surfaces are spheres about the same origin. So it
#:    is that constant area times the band height, and no integral survives.
#: 2. *the taper*, a conical frustum (radius linear from D to F, giving the standard
#:    (r0**2 + r0*r1 + r1**2)/3 mean of squares) bored out by the inner cylinder.
#: 3. *the straight cylinder*, a plain annulus times its height.
#:
#: It is a guard, not a convenience, and it is the same guard `LE10_VOLUME_MM3`
#: provides for its own case: a tetrahedral mesh chords the two spherical surfaces
#: and the whole inner cylinder, so a mesh coarse enough is solving a **smaller
#: body** while still converging beautifully on the wrong answer. Nothing else in a
#: convergence study can tell a discretisation error from a different part. Measured
#: against the OCCT build it agrees to 1e-15 relative (`tests/test_le11_geometry.py`),
#: which is what makes it safe to state as exact rather than as a recorded number.
LE11_VOLUME_MM3: Final = (
    math.pi
    / 4.0
    * (
        (LE11_OUTER_SPHERE_RADIUS_MM**2 - LE11_INNER_SPHERE_RADIUS_MM**2)
        * LE11_SPHERICAL_BAND_HEIGHT_MM
        + LE11_TAPER_HEIGHT_MM
        * (
            (
                LE11_TAPER_BOTTOM_RADIUS_MM**2
                + LE11_TAPER_BOTTOM_RADIUS_MM * LE11_OUTER_CYLINDER_RADIUS_MM
                + LE11_OUTER_CYLINDER_RADIUS_MM**2
            )
            / 3.0
            - LE11_INNER_CYLINDER_RADIUS_MM**2
        )
        + LE11_CYLINDER_HEIGHT_MM
        * (LE11_OUTER_CYLINDER_RADIUS_MM**2 - LE11_INNER_CYLINDER_RADIUS_MM**2)
    )
)


def _le11_sector(angle_rad: float) -> Any:
    """The LE11 meridian revolved about the z axis through `angle_rad`.

    Private, and it takes the angle for one reason: `le11_solid()` is a **quarter**
    model, and "it is a quarter" is a claim a picture cannot settle — a 180 deg
    revolve of the same profile is a plausible-looking body with twice the material
    and the wrong symmetry planes. Exposing the angle lets the test pin the quadrant
    by arithmetic instead, asserting the quarter is exactly half of the 180 deg body.
    """
    from app.kernel.occt.binding import require, symbol

    require()

    def pnt(r: float, z: float) -> Any:
        """A meridian point, in the y = 0 plane."""
        return symbol("gp_Pnt")(r, 0.0, z)

    def arc(radius: float, half_angle_rad: float, start: Any, end: Any) -> Any:
        """A circular arc centred on the origin, through a point at `half_angle_rad`.

        Three-point form rather than `GC_MakeArcOfCircle(gp_Circ, p1, p2, sense)`:
        measured on this OCP build, that overload returns the **major** arc for both
        values of `sense`, so an outer profile built with it wraps 329 deg the wrong
        way round the origin. The midpoint here is placed on the stated circle by
        construction, so the radius and centre are just as explicit, and the arc
        lengths are asserted in the tests.
        """
        middle = pnt(radius * math.cos(half_angle_rad), radius * math.sin(half_angle_rad))
        return symbol("BRepBuilderAPI_MakeEdge")(
            symbol("GC_MakeArcOfCircle")(start, middle, end).Value()
        ).Edge()

    point = {name: pnt(r, z) for name, r, z in LE11_MERIDIAN_MM}
    line = symbol("BRepBuilderAPI_MakeEdge")

    wire = symbol("BRepBuilderAPI_MakeWire")()
    # Inner spherical surface, A up to C. A is on the base plane, C is at 45 deg.
    wire.Add(arc(LE11_INNER_SPHERE_RADIUS_MM, math.pi / 8.0, point["A"], point["C"]))
    # Inner cylindrical surface, C straight up to G. FeenoX splits this at E to get a
    # structured hex mesh; the split is meshing, not geometry, so it is one edge here.
    wire.Add(line(point["C"], point["G"]).Edge())
    wire.Add(line(point["G"], point["H"]).Edge())  # top annulus
    wire.Add(line(point["H"], point["F"]).Edge())  # outer cylindrical surface
    wire.Add(line(point["F"], point["D"]).Edge())  # the taper, one straight segment
    # Outer spherical surface, D down to B.
    wire.Add(
        arc(
            LE11_OUTER_SPHERE_RADIUS_MM,
            math.asin(LE11_SPHERICAL_BAND_HEIGHT_MM / LE11_OUTER_SPHERE_RADIUS_MM) / 2.0,
            point["D"],
            point["B"],
        )
    )
    wire.Add(line(point["B"], point["A"]).Edge())  # base annulus, z = 0

    # The plane is given rather than inferred: `BRepBuilderAPI_MakeFace(wire)` will
    # find a plane for a planar wire, but naming it says which way the face is up
    # and fails loudly if an edge ever leaves y = 0.
    meridian = symbol("BRepBuilderAPI_MakeFace")(
        symbol("gp_Pln")(symbol("gp_Pnt")(0.0, 0.0, 0.0), symbol("gp_Dir")(0.0, 1.0, 0.0)),
        wire.Wire(),
    ).Face()

    axis = symbol("gp_Ax1")(symbol("gp_Pnt")(0.0, 0.0, 0.0), symbol("gp_Dir")(0.0, 0.0, 1.0))
    return symbol("BRepPrimAPI_MakeRevol")(meridian, axis, angle_rad).Shape()


def le11_solid() -> Any:
    """The LE11 quarter model as an OCCT solid, in millimetres.

    One solid occupying the first quadrant (x >= 0, y >= 0), sitting on z = 0 and
    hollow along its axis. The two flat radial faces are the model's symmetry planes,
    the base annulus and the top annulus are the axially restrained faces, and point
    A — `LE11_POINT_A` — is on the boundary where the inner sphere meets the base.
    """
    return _le11_sector(LE11_SECTOR_ANGLE_RAD)


__all__ = [
    "LE11_CYLINDER_HEIGHT_MM",
    "LE11_INNER_CYLINDER_RADIUS_MM",
    "LE11_INNER_SPHERE_RADIUS_MM",
    "LE11_MERIDIAN_MM",
    "LE11_OUTER_CYLINDER_RADIUS_MM",
    "LE11_OUTER_SPHERE_RADIUS_MM",
    "LE11_POINT_A",
    "LE11_SECTOR_ANGLE_RAD",
    "LE11_SPHERICAL_BAND_HEIGHT_MM",
    "LE11_TAPER_BOTTOM_RADIUS_MM",
    "LE11_TAPER_HEIGHT_MM",
    "LE11_TOTAL_HEIGHT_MM",
    "LE11_VOLUME_MM3",
    "le11_solid",
]
