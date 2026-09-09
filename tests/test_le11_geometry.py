"""NAFEMS LE11's solid — the shape, not the code that writes it.

The deliverable of `app/verify/le11_geometry.py` is a body, so every test below
interrogates the built body with OCCT and compares it against a number that came
off `docs/nafems-le11-geometry.md`. That matters more here than on a simpler
benchmark, because every plausible way of getting LE11 wrong still produces a
solid that builds, meshes, solves and draws nicely:

* a **180 deg** revolve instead of 90 deg — twice the material, symmetry planes on
  the wrong faces, a picture nobody would question;
* the profile revolved **solid through the axis** — the bore is the whole
  difference between this benchmark and a lump of steel, and no view from outside
  shows it;
* the spherical band taken as the figure's printed **0.700 m** instead of the
  0.707107 m the same figure's 45 deg annotation forces;
* and the classic: the sourced **metres** used unconverted, or converted twice.

No test here takes a database fixture, so the file runs offline in about a second
and cannot collide with another pytest run.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.verify import le11_geometry as le11

# -- OCCT probes, shared by the shape tests ----------------------------------


def volume_mm3(shape: Any) -> float:
    """The volume OCCT measures for a shape."""
    from app.kernel.occt.binding import symbol

    props = symbol("GProp_GProps")()
    symbol("BRepGProp").VolumeProperties_s(shape, props)
    return float(props.Mass())


def bounding_box(shape: Any) -> tuple[float, float, float, float, float, float]:
    """`(xmin, ymin, zmin, xmax, ymax, zmax)`, with the tolerance gap removed.

    `Bnd_Box` is inflated by the shape's own tolerance when it is built, which puts
    a 1e-7 mm skirt on every face. `SetGap(0.0)` takes it back off; without that a
    test asking "does the body start at x = 0" is really asking about OCCT's
    tolerance policy.
    """
    from app.kernel.occt.binding import symbol

    box = symbol("Bnd_Box")()
    symbol("BRepBndLib").Add_s(shape, box)
    box.SetGap(0.0)
    return tuple(box.Get())  # type: ignore[return-value]


def distance_to_mm(shape: Any, point: tuple[float, float, float]) -> float:
    """Exact minimum distance from a point to a shape, 0 if it is on or in it."""
    from app.kernel.occt.binding import symbol

    vertex = symbol("BRepBuilderAPI_MakeVertex")(symbol("gp_Pnt")(*point)).Vertex()
    extrema = symbol("BRepExtrema_DistShapeShape")(vertex, shape)
    assert extrema.IsDone() and extrema.NbSolution() > 0
    return float(extrema.Value())


@pytest.fixture(scope="module")
def solid() -> Any:
    """The quarter model, built once — the revolve costs a few hundred ms."""
    return le11.le11_solid()


class TestTheDimensionsAreTheSourcedOnesInMillimetres:
    """The sourcing record states LE11 in metres and this repository converts
    nothing anywhere else, so the multiplication by 1000 happens once, in the
    constants, and is pinned here against the metre figures as printed."""

    def test_the_two_spherical_radii_are_one_metre_and_one_point_four_metres(self) -> None:
        assert le11.LE11_INNER_SPHERE_RADIUS_MM == 1000.0
        assert le11.LE11_OUTER_SPHERE_RADIUS_MM == 1400.0

    def test_the_outer_cylinder_radius_is_the_figures_two_hops_from_the_axis(self) -> None:
        """The figure never dimensions it directly: it gives 0.7071 m to the inner
        cylindrical surface and 0.2929 m across the wall."""
        assert le11.LE11_OUTER_CYLINDER_RADIUS_MM == pytest.approx(
            (0.7071 + 0.2929) * 1000.0, abs=1e-9
        )

    def test_the_inner_cylinder_radius_is_the_forty_five_degree_point_on_the_inner_sphere(
        self,
    ) -> None:
        """Printed as 0.7071 m to four decimals; the 45 deg annotation makes it
        exactly R sin 45, and the exact form is what closes the profile."""
        assert le11.LE11_INNER_CYLINDER_RADIUS_MM == pytest.approx(707.1067811865476, abs=1e-9)
        assert le11.LE11_INNER_CYLINDER_RADIUS_MM == pytest.approx(707.1, abs=0.01)

    def test_the_spherical_band_height_takes_the_inferred_value_not_the_printed_one(self) -> None:
        """The figure prints 0.700 m. Taken literally, the junction at radius
        0.7071 m would sit 5.0 mm off the 1.0 m sphere the same figure says it is
        on, so the annotation is what gives. Asserted both ways round: it is the
        exact 45 deg height, and it is *not* the printed 700 mm."""
        assert le11.LE11_SPHERICAL_BAND_HEIGHT_MM == pytest.approx(707.1067811865476, abs=1e-9)
        assert le11.LE11_SPHERICAL_BAND_HEIGHT_MM != 700.0

    def test_the_band_height_is_the_only_reading_that_puts_the_junction_on_the_sphere(
        self,
    ) -> None:
        """The argument that settles the disagreement, as arithmetic rather than as
        a preference between documents."""
        junction = math.hypot(
            le11.LE11_INNER_CYLINDER_RADIUS_MM, le11.LE11_SPHERICAL_BAND_HEIGHT_MM
        )
        assert junction == pytest.approx(le11.LE11_INNER_SPHERE_RADIUS_MM, abs=1e-9)
        printed = math.hypot(le11.LE11_INNER_CYLINDER_RADIUS_MM, 700.0)
        assert le11.LE11_INNER_SPHERE_RADIUS_MM - printed == pytest.approx(5.0, abs=0.05)

    def test_the_taper_is_one_segment_over_the_two_dimensioned_bands(self) -> None:
        """0.345 + 0.345 on the figure, with no geometric feature between them."""
        assert le11.LE11_TAPER_HEIGHT_MM == pytest.approx(690.0, abs=1e-9)

    def test_the_straight_cylinder_is_four_hundred_millimetres_tall(self) -> None:
        assert le11.LE11_CYLINDER_HEIGHT_MM == pytest.approx(400.0, abs=1e-9)

    def test_the_taper_starts_where_the_outer_sphere_reaches_the_band_height(self) -> None:
        """Derived, and FeenoX reaches 1.208305 m by the same construction."""
        assert le11.LE11_TAPER_BOTTOM_RADIUS_MM == pytest.approx(1208.305, abs=0.001)

    def test_the_taper_is_not_tangent_to_the_outer_sphere(self) -> None:
        """D is a genuine slope discontinuity. Filleting it or making the taper
        tangent would be a different body — the perpendicular distance from the
        origin to the taper line is 1.361 m, not the sphere's 1.400 m."""
        d = (le11.LE11_TAPER_BOTTOM_RADIUS_MM, le11.LE11_SPHERICAL_BAND_HEIGHT_MM)
        f = (
            le11.LE11_OUTER_CYLINDER_RADIUS_MM,
            le11.LE11_SPHERICAL_BAND_HEIGHT_MM + le11.LE11_TAPER_HEIGHT_MM,
        )
        dr, dz = f[0] - d[0], f[1] - d[1]
        perpendicular = abs(d[0] * dz - d[1] * dr) / math.hypot(dr, dz)
        assert perpendicular == pytest.approx(1361.0, abs=1.0)
        assert perpendicular < le11.LE11_OUTER_SPHERE_RADIUS_MM

    def test_the_total_height_is_the_three_bands_added_up(self) -> None:
        assert le11.LE11_TOTAL_HEIGHT_MM == pytest.approx(1797.1067811865476, abs=1e-9)

    def test_the_sector_is_ninety_degrees(self) -> None:
        assert le11.LE11_SECTOR_ANGLE_RAD == pytest.approx(math.radians(90.0), abs=1e-15)

    def test_point_a_is_on_the_base_plane_one_metre_from_the_axis(self) -> None:
        """Where the -105 MPa axial stress is read."""
        assert le11.LE11_POINT_A == (1000.0, 0.0, 0.0)

    def test_the_meridian_carries_the_seven_sourced_corners_in_profile_order(self) -> None:
        names = tuple(name for name, _, _ in le11.LE11_MERIDIAN_MM)
        assert names == ("A", "C", "G", "H", "F", "D", "B")

    def test_every_meridian_corner_matches_the_sourcing_records_table(self) -> None:
        """Section 1.4 of the sourcing record, in metres, times 1000."""
        expected = {
            "A": (1.000000, 0.000000),
            "B": (1.400000, 0.000000),
            "C": (0.707107, 0.707107),
            "D": (1.208305, 0.707107),
            "F": (1.000000, 1.397107),
            "G": (0.707107, 1.797107),
            "H": (1.000000, 1.797107),
        }
        built = {name: (r, z) for name, r, z in le11.LE11_MERIDIAN_MM}
        assert set(built) == set(expected)
        for name, (r_m, z_m) in expected.items():
            assert built[name][0] == pytest.approx(r_m * 1000.0, abs=0.001)
            assert built[name][1] == pytest.approx(z_m * 1000.0, abs=0.001)


class TestTheVolumeIsDerivedAndTheBuiltSolidAgreesWithIt:
    """`LE11_VOLUME_MM3` is closed-form, and it exists to catch a mesh that has
    chorded the curved boundaries so coarsely it is a smaller body."""

    def test_the_derived_volume_is_reproduced_by_an_independent_integration(self) -> None:
        """Recomputed here from the sourced metre figures rather than from the
        module's constants, and by a different route — a numeric integral of the
        annular area up the axis rather than three closed forms. Agreement to five
        significant figures is not the constant checking itself."""
        band = 1000.0 * math.sqrt(0.5)
        top_of_taper = band + 690.0
        top = top_of_taper + 400.0
        taper_bottom_radius = math.sqrt(1400.0**2 - band**2)

        def inner(z: float) -> float:
            return math.sqrt(1000.0**2 - z**2) if z <= band else band

        def outer(z: float) -> float:
            if z <= band:
                return math.sqrt(1400.0**2 - z**2)
            if z <= top_of_taper:
                fraction = (z - band) / 690.0
                return taper_bottom_radius + fraction * (1000.0 - taper_bottom_radius)
            return 1000.0

        steps = 600_000
        step = top / steps
        total = 0.0
        for index in range(steps):
            z = (index + 0.5) * step
            total += (outer(z) ** 2 - inner(z) ** 2) * step
        assert le11.LE11_VOLUME_MM3 == pytest.approx(math.pi / 4.0 * total, rel=1e-5)

    def test_the_built_solid_has_a_positive_finite_volume(self, solid: Any) -> None:
        measured = volume_mm3(solid)
        assert math.isfinite(measured)
        assert measured > 0.0

    def test_the_built_volume_matches_the_derived_volume(self, solid: Any) -> None:
        """Tight on purpose: OCCT integrates the exact analytic surfaces, so this is
        not a numerical-agreement test — anything past the last few bits means the
        body that got built is not the body the constants describe."""
        assert volume_mm3(solid) == pytest.approx(le11.LE11_VOLUME_MM3, rel=1e-9)


class TestTheBodyIsAQuarterModel:
    def test_it_builds_as_exactly_one_valid_solid(self, solid: Any) -> None:
        from app.kernel.occt.binding import symbol

        assert symbol("BRepCheck_Analyzer")(solid).IsValid()
        assert solid.ShapeType() == symbol("TopAbs_ShapeEnum").TopAbs_SOLID

    def test_it_lies_in_one_quadrant(self, solid: Any) -> None:
        """A 90 deg revolve of a profile at x > 0 about z reaches +y and no further.
        Any negative extent means the sector was swept the wrong way or too far."""
        xmin, ymin, _, _, _, _ = bounding_box(solid)
        assert xmin == pytest.approx(0.0, abs=1e-6)
        assert ymin == pytest.approx(0.0, abs=1e-6)

    def test_its_x_and_y_extents_are_both_the_outer_sphere_radius(self, solid: Any) -> None:
        """The widest point of the body is the base outer edge, on the outer sphere.
        The quadrant contains both of its bounding radii, so x and y must agree."""
        _, _, _, xmax, ymax, _ = bounding_box(solid)
        assert xmax == pytest.approx(le11.LE11_OUTER_SPHERE_RADIUS_MM, abs=1e-6)
        assert ymax == pytest.approx(le11.LE11_OUTER_SPHERE_RADIUS_MM, abs=1e-6)

    def test_the_quarter_is_exactly_half_of_the_same_profile_revolved_through_180(self) -> None:
        """The quadrant pinned by arithmetic rather than by a picture. Every other
        test in this file passes just as happily on a 180 deg body scaled to look
        right, and a rendered view of a half model is not obviously wrong."""
        quarter = volume_mm3(le11.le11_solid())
        half = volume_mm3(le11._le11_sector(math.pi))
        assert quarter == pytest.approx(half / 2.0, rel=1e-9)

    def test_the_axial_extent_is_the_sum_of_the_three_meridian_heights(self, solid: Any) -> None:
        _, _, zmin, _, _, zmax = bounding_box(solid)
        assert zmin == pytest.approx(0.0, abs=1e-6)
        assert zmax - zmin == pytest.approx(le11.LE11_TOTAL_HEIGHT_MM, abs=1e-6)
        assert zmax - zmin == pytest.approx(
            le11.LE11_SPHERICAL_BAND_HEIGHT_MM
            + le11.LE11_TAPER_HEIGHT_MM
            + le11.LE11_CYLINDER_HEIGHT_MM,
            abs=1e-6,
        )


class TestTheBodyIsHollow:
    """LE11 is bored out along its axis. Nothing seen from outside says so, and a
    body revolved solid through the axis passes every extent and quadrant test
    above — so the void is measured directly."""

    def test_there_is_no_material_on_the_axis(self, solid: Any) -> None:
        """A point on the axis at mid-taper height. On a solid body the distance
        would be 0; here the nearest material is the inner cylindrical wall, so the
        measurement doubles as a check on the inner radius."""
        on_axis = (0.0, 0.0, 1000.0)
        assert distance_to_mm(solid, on_axis) == pytest.approx(
            le11.LE11_INNER_CYLINDER_RADIUS_MM, abs=1e-6
        )

    def test_the_bore_reaches_the_full_height_of_the_body(self, solid: Any) -> None:
        """Sampled the length of the axis, including just under the top face — a
        blind bore stopping short would be a different part."""
        for fraction in (0.01, 0.25, 0.5, 0.75, 0.99):
            height = fraction * le11.LE11_TOTAL_HEIGHT_MM
            assert distance_to_mm(solid, (0.0, 0.0, height)) > 0.0

    def test_the_origin_sees_the_inner_spherical_surface_at_its_sourced_radius(
        self, solid: Any
    ) -> None:
        """From the centre of both spheres, the nearest material is the inner
        spherical surface — so this distance *is* the inner sphere radius, and it
        would be 0 on a body with no bore."""
        assert distance_to_mm(solid, (0.0, 0.0, 0.0)) == pytest.approx(
            le11.LE11_INNER_SPHERE_RADIUS_MM, abs=1e-6
        )


class TestTheExtremeRadiiAreWhatTheSourcesSay:
    def test_there_is_material_just_inside_the_outer_sphere_at_the_equator(
        self, solid: Any
    ) -> None:
        """One millimetre in from the widest point of the body, on the base plane."""
        just_inside = (le11.LE11_OUTER_SPHERE_RADIUS_MM - 1.0, 0.0, 0.0)
        assert distance_to_mm(solid, just_inside) == pytest.approx(0.0, abs=1e-9)

    def test_there_is_no_material_just_outside_the_outer_sphere_at_the_equator(
        self, solid: Any
    ) -> None:
        """One millimetre out, and it must be one millimetre of clear air — not
        merely 'outside', which a body 10% too small would also satisfy."""
        just_outside = (le11.LE11_OUTER_SPHERE_RADIUS_MM + 1.0, 0.0, 0.0)
        assert distance_to_mm(solid, just_outside) == pytest.approx(1.0, abs=1e-6)

    def test_there_is_no_material_just_outside_the_cylinder_at_the_top(self, solid: Any) -> None:
        """The straight cylinder is narrower than the base, so the bounding box
        cannot speak for it."""
        near_top = le11.LE11_TOTAL_HEIGHT_MM - 1.0
        assert distance_to_mm(
            solid, (le11.LE11_OUTER_CYLINDER_RADIUS_MM - 1.0, 0.0, near_top)
        ) == pytest.approx(0.0, abs=1e-9)
        assert distance_to_mm(
            solid, (le11.LE11_OUTER_CYLINDER_RADIUS_MM + 1.0, 0.0, near_top)
        ) == pytest.approx(1.0, abs=1e-6)


class TestPointAIsOnTheBoundaryWhereTheTargetIsRead:
    def test_point_a_lies_on_the_solid(self, solid: Any) -> None:
        assert distance_to_mm(solid, le11.LE11_POINT_A) == pytest.approx(0.0, abs=1e-9)

    def test_half_a_millimetre_inboard_of_point_a_is_outside_the_body(self, solid: Any) -> None:
        """The other half of "on the boundary": A is bracketed to half a millimetre
        by material on one side of it and clear air on the other. A point merely
        *inside* the body would pass the test above on its own."""
        inboard = (le11.LE11_POINT_A[0] - 0.5, 0.0, 0.0)
        assert distance_to_mm(solid, inboard) == pytest.approx(0.5, abs=1e-6)

    def test_half_a_millimetre_outboard_of_point_a_is_on_the_base_annulus(
        self, solid: Any
    ) -> None:
        outboard = (le11.LE11_POINT_A[0] + 0.5, 0.0, 0.0)
        assert distance_to_mm(solid, outboard) == pytest.approx(0.0, abs=1e-9)
