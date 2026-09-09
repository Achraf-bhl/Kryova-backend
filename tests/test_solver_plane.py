"""Plane-stress and plane-strain element verification.

Every number asserted here is a closed-form result, not a previously recorded
solver output. A golden file would record whatever the solver did on the day it
was written; these fail when the physics changes, which is the only thing worth
being told.

The closed forms used, and what each one is here to catch:

* **Uniaxial tension of a strip** -- `sigma = F/A`, `delta = FL/AE`. Exact for
  both element orders, because the exact displacement field is linear and lies
  in both FE spaces. It pins the assembly, the tributary edge load and the
  stress recovery all at once, at full precision.
* **The out-of-plane answer** -- plane stress has `SZZ = 0` and a real
  through-thickness contraction; plane strain has `SZZ = nu (SXX + SYY)` and
  none. This is the whole difference between the two idealisations and it is the
  one that produces a plausible wrong number rather than an error.
* **Pure shear of a square** -- recovers `G = E / (2 (1 + nu))` from a solved
  displacement, so a typo in the shear term of either D matrix cannot agree with
  itself.
* **Lame's thick-walled cylinder** -- the closed form with a *curved* boundary,
  which is what NAFEMS LE1 will actually exercise.
* **A fully restrained heated slice** -- `sigma = -E alpha dT / (1 - nu)` for
  plane stress and `-E alpha dT / (1 - 2 nu)` for plane strain.

Everything in this file runs offline in about a second: no database fixture, no
gmsh, no network. The meshes are built here in closed form so a refinement study
is exact rather than at the mesher's discretion.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.planar import TRI6_EDGES, TriMesh
from app.solve.base import Solver
from app.solve.materials import MATERIALS
from app.solve.plane import (
    PlanarSolver,
    PlaneCase,
    PlaneSolver,
    PlaneState,
    assemble_loads,
    assemble_stiffness,
    constitutive_matrix,
    in_plane_dofs,
    plane_strain_matrix,
    plane_stress_matrix,
)
from app.solve.types import (
    BoxSelector,
    CylinderSelector,
    FaceSelector,
    Fixture,
    ForceLoad,
    GravityLoad,
    Material,
    PressureLoad,
    SolverError,
    SphereSelector,
)

STEEL = MATERIALS["steel-1018"]
E = STEEL.youngs_modulus_mpa
NU = STEEL.poissons_ratio


# -- meshes ------------------------------------------------------------------
#
# Built in closed form rather than meshed, so a refinement study changes exactly
# one thing and the node positions are exact numbers a closed form can be read
# at. `app/mesh/` has no planar mesher yet; when it grows one these stay, for the
# same reason `app/mesh/primitives.box_mesh` stayed after gmsh arrived.


def rectangle_mesh(length: float, height: float, nx: int, ny: int) -> TriMesh:
    """A rectangle [0, length] x [0, height] as anticlockwise tri3 elements."""
    xs = np.linspace(0.0, length, nx + 1)
    ys = np.linspace(0.0, height, ny + 1)
    grid_x, grid_y = np.meshgrid(xs, ys, indexing="ij")
    nodes = np.column_stack(
        [grid_x.ravel(), grid_y.ravel(), np.zeros(grid_x.size, dtype=np.float64)]
    )

    def index(i: int, j: int) -> int:
        return i * (ny + 1) + j

    tris = []
    for i in range(nx):
        for j in range(ny):
            a, b, c, d = index(i, j), index(i + 1, j), index(i + 1, j + 1), index(i, j + 1)
            tris.append([a, b, c])
            tris.append([a, c, d])
    return TriMesh(nodes=nodes, tris=np.array(tris, dtype=np.int64))


def annulus_quarter(inner: float, outer: float, radial: int, angular: int) -> TriMesh:
    """A quarter annulus in the first quadrant, anticlockwise tri3 elements.

    The corner nodes sit exactly on the two circles, so the bore radius a
    selector names is the radius the mesh actually has -- the polygonal chords
    between them are the only geometry error, and it is O(h^2).
    """
    radii = np.linspace(inner, outer, radial + 1)
    thetas = np.linspace(0.0, 0.5 * np.pi, angular + 1)
    r_grid, t_grid = np.meshgrid(radii, thetas, indexing="ij")
    nodes = np.column_stack(
        [
            (r_grid * np.cos(t_grid)).ravel(),
            (r_grid * np.sin(t_grid)).ravel(),
            np.zeros(r_grid.size, dtype=np.float64),
        ]
    )

    def index(i: int, j: int) -> int:
        return i * (angular + 1) + j

    tris = []
    for i in range(radial):
        for j in range(angular):
            a, b, c, d = index(i, j), index(i + 1, j), index(i + 1, j + 1), index(i, j + 1)
            tris.append([a, b, c])
            tris.append([a, c, d])
    return TriMesh(nodes=nodes, tris=np.array(tris, dtype=np.int64))


def promote_to_tri6(mesh: TriMesh) -> TriMesh:
    """Add a midside node at the chord midpoint of every edge.

    Straight-sided (subparametric), which is what makes the Gauss rule in
    `app/solve/plane.py` exact: the mapping stays affine. On a curved boundary
    it also means the mesh is a polygon, and the resulting geometry error is
    stated where the Lame case is asserted rather than hidden in a tolerance.
    """
    nodes = [row.copy() for row in mesh.nodes]
    lookup: dict[tuple[int, int], int] = {}
    midside = np.zeros((len(mesh.tris), 3), dtype=np.int64)
    for element, tri in enumerate(mesh.tris):
        for local, (a, b) in enumerate(TRI6_EDGES):
            key = (min(int(tri[a]), int(tri[b])), max(int(tri[a]), int(tri[b])))
            if key not in lookup:
                lookup[key] = len(nodes)
                nodes.append(0.5 * (mesh.nodes[key[0]] + mesh.nodes[key[1]]))
            midside[element, local] = lookup[key]
    return TriMesh(
        nodes=np.array(nodes, dtype=np.float64), tris=mesh.tris.copy(), midside=midside
    )


def point_fixture(x: float, y: float, dofs: list[str], name: str) -> Fixture:
    """A single node held in the named directions -- the minimum a plane model
    needs on top of an edge restraint to remove the last rigid-body motion.

    The radius is deliberately tiny. A sphere wide enough to also catch the
    neighbouring midside node holds three nodes rather than one, which
    over-constrains the strip: the closed forms below then stop being exact and
    the failure looks like a solver that is a percent off near the fixed end
    rather than like a badly posed test.
    """
    return Fixture(
        where=SphereSelector(centre=(x, y, 0.0), radius=1e-6),
        dofs=dofs,  # type: ignore[arg-type]
        name=name,
    )


# -- uniaxial tension --------------------------------------------------------

LENGTH = 100.0
HEIGHT = 20.0
THICKNESS = 5.0
PULL_N = 5_000.0
SIGMA = PULL_N / (HEIGHT * THICKNESS)  # 50 MPa


def uniaxial_case(state: PlaneState) -> PlaneCase:
    """Strip pulled along +x, restrained so it may still contract laterally.

    An x-roller on the whole left edge plus one node held in y: an encastre on
    the left edge would also block the Poisson contraction and raise a stress
    concentration next to it, so the stress would no longer be uniform and there
    would be no closed form to check against.
    """
    return PlaneCase(
        name="uniaxial",
        material=STEEL,
        thickness_mm=THICKNESS,
        state=state,
        fixtures=[
            Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"], name="left roller"),
            point_fixture(0.0, 0.0, ["y"], "origin"),
        ],
        loads=[
            ForceLoad(
                where=FaceSelector(axis="x", side="max"),
                force_n=(PULL_N, 0.0, 0.0),
                name="pull",
            )
        ],
    )


@pytest.fixture(scope="module")
def strip_tri3() -> TriMesh:
    return rectangle_mesh(LENGTH, HEIGHT, 10, 2)


@pytest.fixture(scope="module")
def strip_tri6(strip_tri3: TriMesh) -> TriMesh:
    return promote_to_tri6(strip_tri3)


class TestUniaxialTension:
    """A prismatic strip in pure tension is exactly representable by both
    element orders, so these compare against the textbook answer at full
    precision rather than to a percent or two."""

    @pytest.mark.parametrize("order", ["tri3", "tri6"])
    def test_the_axial_stress_is_force_over_area(
        self, order: str, strip_tri3: TriMesh, strip_tri6: TriMesh
    ) -> None:
        mesh = strip_tri3 if order == "tri3" else strip_tri6
        output = PlaneSolver().solve(mesh, uniaxial_case(PlaneState.STRESS))
        assert output.nodal_stress is not None
        sxx = output.nodal_stress[:, 0]
        assert sxx == pytest.approx(SIGMA, rel=1e-9)

    @pytest.mark.parametrize("order", ["tri3", "tri6"])
    def test_the_extension_is_f_l_over_a_e(
        self, order: str, strip_tri3: TriMesh, strip_tri6: TriMesh
    ) -> None:
        mesh = strip_tri3 if order == "tri3" else strip_tri6
        output = PlaneSolver().solve(mesh, uniaxial_case(PlaneState.STRESS))
        loaded = np.flatnonzero(np.isclose(mesh.nodes[:, 0], LENGTH))
        extension = float(output.displacements[loaded, 0].mean())
        assert extension == pytest.approx(SIGMA * LENGTH / E, rel=1e-9)

    @pytest.mark.parametrize("order", ["tri3", "tri6"])
    def test_plane_strain_is_stiffer_by_one_minus_nu_squared(
        self, order: str, strip_tri3: TriMesh, strip_tri6: TriMesh
    ) -> None:
        # A long slice cannot contract out of plane, so the same pull stretches
        # it less: delta = sigma L (1 - nu^2) / E.
        mesh = strip_tri3 if order == "tri3" else strip_tri6
        output = PlaneSolver().solve(mesh, uniaxial_case(PlaneState.STRAIN))
        loaded = np.flatnonzero(np.isclose(mesh.nodes[:, 0], LENGTH))
        extension = float(output.displacements[loaded, 0].mean())
        expected = SIGMA * LENGTH * (1.0 - NU * NU) / E
        assert extension == pytest.approx(expected, rel=1e-9)

    def test_the_transverse_strain_is_minus_nu_sigma_over_e_in_plane_stress(
        self, strip_tri6: TriMesh
    ) -> None:
        output = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRESS))
        top = np.flatnonzero(np.isclose(strip_tri6.nodes[:, 1], HEIGHT))
        transverse = float(output.displacements[top, 1].mean()) / HEIGHT
        assert transverse == pytest.approx(-NU * SIGMA / E, rel=1e-9)

    def test_the_transverse_strain_carries_one_plus_nu_in_plane_strain(
        self, strip_tri6: TriMesh
    ) -> None:
        # eps_yy = -(1 + nu) nu sigma / E: the out-of-plane restraint puts the
        # slice into through-thickness tension, which Poisson feeds back in
        # plane. Reading it as -nu sigma / E understates the contraction by 29%
        # for steel.
        output = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRAIN))
        top = np.flatnonzero(np.isclose(strip_tri6.nodes[:, 1], HEIGHT))
        transverse = float(output.displacements[top, 1].mean()) / HEIGHT
        assert transverse == pytest.approx(-(1.0 + NU) * NU * SIGMA / E, rel=1e-9)

    def test_the_displacement_field_has_no_out_of_plane_component(
        self, strip_tri6: TriMesh
    ) -> None:
        output = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRESS))
        assert output.displacements.shape == (strip_tri6.node_count, 3)
        assert output.displacements[:, 2] == pytest.approx(0.0, abs=0.0)

    def test_the_summary_volume_is_area_times_thickness(self, strip_tri6: TriMesh) -> None:
        output = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRESS))
        assert output.result.volume_mm3 == pytest.approx(LENGTH * HEIGHT * THICKNESS)
        assert output.result.element_count == strip_tri6.element_count
        assert output.result.max_von_mises_mpa == pytest.approx(SIGMA, rel=1e-9)


class TestTheOutOfPlaneStress:
    """SZZ is the single number that separates the two idealisations, and it is
    the one an implementation gets wrong without failing anything: the in-plane
    answers stay right and only von Mises quietly moves."""

    def test_plane_stress_reports_szz_zero(self, strip_tri6: TriMesh) -> None:
        output = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRESS))
        assert output.nodal_stress is not None
        assert output.nodal_stress[:, 2] == pytest.approx(0.0, abs=1e-12)

    def test_plane_strain_reports_szz_as_nu_times_the_in_plane_trace(
        self, strip_tri6: TriMesh
    ) -> None:
        output = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRAIN))
        assert output.nodal_stress is not None
        sxx, syy, szz = output.nodal_stress[:, 0], output.nodal_stress[:, 1], output.nodal_stress[:, 2]
        assert szz == pytest.approx(NU * (sxx + syy), rel=1e-9, abs=1e-12)
        # And for this load case that is a specific number, not just a relation.
        assert szz == pytest.approx(NU * SIGMA, rel=1e-9)

    def test_the_out_of_plane_strain_is_zero_only_in_plane_strain(
        self, strip_tri6: TriMesh
    ) -> None:
        # eps_zz is not in the output -- it is the idealisation's *premise*, so
        # the testable statement is what it implies about the tensor that is:
        # eps_zz = (SZZ - nu (SXX + SYY)) / E.
        def out_of_plane_strain(state: PlaneState) -> np.ndarray:
            output = PlaneSolver().solve(strip_tri6, uniaxial_case(state))
            assert output.nodal_stress is not None
            sxx, syy, szz = (
                output.nodal_stress[:, 0],
                output.nodal_stress[:, 1],
                output.nodal_stress[:, 2],
            )
            return (szz - NU * (sxx + syy)) / E

        assert out_of_plane_strain(PlaneState.STRAIN) == pytest.approx(0.0, abs=1e-18)
        assert out_of_plane_strain(PlaneState.STRESS) == pytest.approx(
            -NU * SIGMA / E, rel=1e-9
        )

    def test_von_mises_differs_between_the_two_states(self, strip_tri6: TriMesh) -> None:
        # The consequence a reader actually sees. Uniaxial plane stress gives
        # von Mises == sigma; plane strain carries SZZ = nu sigma, and the
        # invariant of (sigma, 0, nu sigma) is sigma sqrt(1 - nu + nu^2) --
        # 89.1% of sigma for steel. Reporting SZZ as zero would put it back at
        # sigma and look like the model was simply a little conservative.
        loose = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRESS))
        held = PlaneSolver().solve(strip_tri6, uniaxial_case(PlaneState.STRAIN))
        assert loose.von_mises == pytest.approx(SIGMA, rel=1e-9)
        assert held.von_mises == pytest.approx(
            SIGMA * np.sqrt(1.0 - NU + NU * NU), rel=1e-9
        )


# -- pure shear --------------------------------------------------------------

SHEAR_SIDE = 40.0
SHEAR_THICKNESS = 3.0
SHEAR_MPA = 20.0


def pure_shear_case(mesh: TriMesh) -> PlaneCase:
    """A square in a uniform shear stress state, from tractions on all four edges.

    Self-equilibrated, so the three fixtures carry no reaction and the exact
    solution `u = (gamma y, 0)` satisfies them exactly. `gamma = tau / G` is
    then read straight off the top edge.
    """
    edge_force = SHEAR_MPA * SHEAR_THICKNESS * SHEAR_SIDE
    return PlaneCase(
        name="pure shear",
        material=STEEL,
        thickness_mm=SHEAR_THICKNESS,
        state=PlaneState.STRESS,
        fixtures=[
            point_fixture(0.0, 0.0, ["x", "y"], "origin"),
            # Removes the last rigid-body motion, the rotation about z, without
            # restraining any strain: the exact solution has u_y = 0 here.
            point_fixture(SHEAR_SIDE, 0.0, ["y"], "no spin"),
        ],
        loads=[
            ForceLoad(
                where=FaceSelector(axis="y", side="min"),
                force_n=(-edge_force, 0.0, 0.0),
                name="bottom",
            ),
            ForceLoad(
                where=FaceSelector(axis="y", side="max"),
                force_n=(edge_force, 0.0, 0.0),
                name="top",
            ),
            ForceLoad(
                where=FaceSelector(axis="x", side="min"),
                force_n=(0.0, -edge_force, 0.0),
                name="left",
            ),
            ForceLoad(
                where=FaceSelector(axis="x", side="max"),
                force_n=(0.0, edge_force, 0.0),
                name="right",
            ),
        ],
    )


class TestPureShear:
    @pytest.mark.parametrize("order", ["tri3", "tri6"])
    def test_the_shear_modulus_comes_back_as_e_over_two_one_plus_nu(self, order: str) -> None:
        mesh = rectangle_mesh(SHEAR_SIDE, SHEAR_SIDE, 8, 8)
        if order == "tri6":
            mesh = promote_to_tri6(mesh)
        output = PlaneSolver().solve(mesh, pure_shear_case(mesh))

        top = np.flatnonzero(np.isclose(mesh.nodes[:, 1], SHEAR_SIDE))
        gamma = float(output.displacements[top, 0].mean()) / SHEAR_SIDE
        measured_g = SHEAR_MPA / gamma
        assert measured_g == pytest.approx(E / (2.0 * (1.0 + NU)), rel=1e-9)

    def test_the_stress_state_is_pure_shear(self) -> None:
        mesh = promote_to_tri6(rectangle_mesh(SHEAR_SIDE, SHEAR_SIDE, 8, 8))
        output = PlaneSolver().solve(mesh, pure_shear_case(mesh))
        assert output.nodal_stress is not None
        assert output.nodal_stress[:, 0] == pytest.approx(0.0, abs=1e-9)
        assert output.nodal_stress[:, 1] == pytest.approx(0.0, abs=1e-9)
        assert output.nodal_stress[:, 3] == pytest.approx(SHEAR_MPA, rel=1e-9)


# -- Lame's thick-walled cylinder --------------------------------------------

BORE_MM = 50.0
OUTER_MM = 100.0
INTERNAL_MPA = 10.0


def lame_hoop_at_bore() -> float:
    """`p (b^2 + a^2) / (b^2 - a^2)` -- Lame, at the bore."""
    return INTERNAL_MPA * (OUTER_MM**2 + BORE_MM**2) / (OUTER_MM**2 - BORE_MM**2)


def lame_case() -> PlaneCase:
    """A quarter of a long cylinder: symmetry rollers on both cut faces, the
    pressure on the bore. Plane strain, because that is what "long" means."""
    return PlaneCase(
        name="Lame",
        material=STEEL,
        thickness_mm=1.0,  # a unit slice, stated rather than assumed
        state=PlaneState.STRAIN,
        fixtures=[
            # `tolerance` is tightened from the 1% default on purpose. The
            # default band is 1% of the bounding box, 1 mm here, and on the
            # finest mesh in the study below the midside node just off the
            # theta = 0 ray sits 0.82 mm from it -- so the default silently
            # holds a second row of nodes and stiffens the symmetry plane. It
            # showed up as the hoop stress getting *worse* under refinement,
            # which is the shape of a badly posed model rather than of a
            # converging one.
            Fixture(
                where=FaceSelector(axis="y", side="min", tolerance=1e-3),
                kind="symmetry",
                normal="y",
                name="theta = 0",
            ),
            Fixture(
                where=FaceSelector(axis="x", side="min", tolerance=1e-3),
                kind="symmetry",
                normal="x",
                name="theta = 90",
            ),
        ],
        loads=[
            PressureLoad(
                where=CylinderSelector(
                    axis_point=(0.0, 0.0, 0.0),
                    axis_direction=(0.0, 0.0, 1.0),
                    radius=BORE_MM,
                    radius_tolerance=1.0,
                ),
                # **Positive**, and the sign is worth pausing on because it
                # reads backwards. `PressureLoad`'s positive sense pushes *into
                # the material*, i.e. along `-n` where `n` is the material's
                # outward normal. At a bore the material's outward normal points
                # at the axis, so "into the material" is radially *outward* --
                # which is exactly what a pressurised fluid inside the bore
                # does. Writing -p here puts the cylinder into compression and
                # produces the Lame hoop stress with the wrong sign and very
                # nearly the right magnitude.
                pressure_mpa=INTERNAL_MPA,
                name="internal pressure",
            )
        ],
    )


def polar_components(
    nodes: np.ndarray, stress: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(radial, hoop) stress at each node, from the Cartesian tensor."""
    theta = np.arctan2(nodes[:, 1], nodes[:, 0])
    cos, sin = np.cos(theta), np.sin(theta)
    sxx, syy, sxy = stress[:, 0], stress[:, 1], stress[:, 3]
    radial = sxx * cos**2 + syy * sin**2 + 2.0 * sxy * sin * cos
    hoop = sxx * sin**2 + syy * cos**2 - 2.0 * sxy * sin * cos
    return radial, hoop


def bore_hoop_stress(mesh: TriMesh) -> float:
    """Mean hoop stress over the bore nodes of a solved quarter cylinder."""
    output = PlaneSolver().solve(mesh, lame_case())
    radius = np.linalg.norm(mesh.nodes[:, :2], axis=1)
    bore = np.flatnonzero(np.isclose(radius, BORE_MM, rtol=1e-9))
    assert output.nodal_stress is not None
    _, hoop = polar_components(mesh.nodes, output.nodal_stress)
    return float(hoop[bore].mean())


@pytest.fixture(scope="module")
def lame_solved() -> tuple[TriMesh, object]:
    mesh = promote_to_tri6(annulus_quarter(BORE_MM, OUTER_MM, 8, 24))
    return mesh, PlaneSolver().solve(mesh, lame_case())


class TestTheThickWalledCylinder:
    """The closed form with a curved boundary, which is what NAFEMS LE1 will
    exercise. Two things are being checked at once and they fail differently: a
    wrong constitutive matrix moves the hoop stress everywhere, while a wrong
    edge normal or a wrong tributary length moves the radial stress at the bore
    off `-p` while leaving the hoop stress looking plausible.

    Tolerances are **stated rather than tuned**, and the measurements they were
    fixed around are recorded here so a later drift is visible: on the 8 x 24
    tri6 quarter model the bore hoop stress comes out at 16.6067 MPa against
    Lame's 16.6667, **0.360% low**, and the bore radial stress at -9.9224
    against -10, 0.776% low. Two errors are in there and both shrink together:
    the mesh is a polygon inscribed in the bore, worth about `(dtheta)^2 / 8` =
    0.05% at 24 divisions over the quadrant, and the discretisation itself. The
    bands below are 1% and 5%, which is comfortably wider than either and
    comfortably narrower than what a real defect produces -- every sign or
    constitutive error found while writing this file was off by 30% or more.
    """

    def test_the_hoop_stress_at_the_bore_matches_lame(self, lame_solved) -> None:  # type: ignore[no-untyped-def]
        mesh, _ = lame_solved
        assert bore_hoop_stress(mesh) == pytest.approx(lame_hoop_at_bore(), rel=0.01)

    def test_the_hoop_stress_converges_under_refinement(self) -> None:
        # A single number inside a band could be a coincidence between two
        # errors. Three levels that improve monotonically cannot be: measured
        # 1.049%, 0.360%, 0.107% low, an observed order of about 1.8.
        target = lame_hoop_at_bore()
        errors = [
            abs(bore_hoop_stress(promote_to_tri6(annulus_quarter(BORE_MM, OUTER_MM, nr, nt))) - target)
            / target
            for nr, nt in ((4, 12), (8, 24), (16, 48))
        ]
        assert errors[0] > errors[1] > errors[2]
        assert errors[2] < 0.002

    def test_the_linear_triangle_is_the_stiffer_element_here(self) -> None:
        # tri3 is constant-strain, so it cannot represent the hoop stress
        # gradient through the wall and under-reads the bore: -3.883% against
        # tri6's -0.360% on the same 8 x 24 grid. The comparison is at equal
        # element count, so it measures the element and not two discretisations.
        grid = annulus_quarter(BORE_MM, OUTER_MM, 8, 24)
        target = lame_hoop_at_bore()
        linear = abs(bore_hoop_stress(grid) - target)
        quadratic = abs(bore_hoop_stress(promote_to_tri6(grid)) - target)
        assert quadratic < linear
        assert linear / target < 0.05

    def test_the_radial_stress_at_the_bore_is_minus_the_pressure(self, lame_solved) -> None:  # type: ignore[no-untyped-def]
        # The boundary condition itself, and the one that fails differently
        # from the rest: it comes out right only if the outward normal was
        # recovered from the edge winding correctly and the pressure sign
        # convention was honoured. Both errors leave the hoop stress looking
        # plausible — one of them merely negates it.
        mesh, output = lame_solved
        radius = np.linalg.norm(mesh.nodes[:, :2], axis=1)
        bore = np.flatnonzero(np.isclose(radius, BORE_MM, rtol=1e-9))
        radial, _ = polar_components(mesh.nodes, output.nodal_stress)
        assert float(radial[bore].mean()) == pytest.approx(-INTERNAL_MPA, rel=0.05)

    def test_the_outer_wall_is_traction_free(self, lame_solved) -> None:  # type: ignore[no-untyped-def]
        mesh, output = lame_solved
        radius = np.linalg.norm(mesh.nodes[:, :2], axis=1)
        outer = np.flatnonzero(np.isclose(radius, OUTER_MM, rtol=1e-9))
        radial, _ = polar_components(mesh.nodes, output.nodal_stress)
        assert float(np.abs(radial[outer]).max()) < 0.05 * INTERNAL_MPA

    def test_the_hoop_stress_follows_lame_through_the_wall(self, lame_solved) -> None:  # type: ignore[no-untyped-def]
        # Not just the peak: the whole radial distribution, which is what
        # distinguishes "the boundary condition is right" from "the elasticity
        # is right".
        mesh, output = lame_solved
        radius = np.linalg.norm(mesh.nodes[:, :2], axis=1)
        _, hoop = polar_components(mesh.nodes, output.nodal_stress)
        factor = INTERNAL_MPA * BORE_MM**2 / (OUTER_MM**2 - BORE_MM**2)
        for sample in (62.5, 75.0, 87.5):  # node rings of the 8-division wall
            ring = np.flatnonzero(np.isclose(radius, sample, rtol=1e-6))
            assert len(ring) > 0
            expected = factor * (1.0 + OUTER_MM**2 / sample**2)
            assert float(hoop[ring].mean()) == pytest.approx(expected, rel=0.02)


# -- the load is mesh-independent -------------------------------------------


class TestTheAppliedLoadDoesNotDependOnTheMesh:
    """Tributary weighting exists for exactly this property. `assemble_loads` is
    public so it can be weighed without solving -- an invariant nobody can read
    the value of is an invariant nobody checks."""

    def total(self, mesh: TriMesh, case: PlaneCase) -> np.ndarray:
        forces, _ = assemble_loads(mesh, case, 1.0)
        return forces.reshape(-1, 2).sum(axis=0)

    def pressure_case(self) -> PlaneCase:
        return PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
            loads=[
                PressureLoad(
                    where=FaceSelector(axis="x", side="max"),
                    pressure_mpa=-2.5,
                    name="suction",
                )
            ],
        )

    @pytest.mark.parametrize("divisions", [(4, 1), (10, 3), (25, 7)])
    def test_a_pressure_integrates_to_p_times_the_edge_area(
        self, divisions: tuple[int, int]
    ) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, *divisions)
        expected = np.array([2.5 * HEIGHT * THICKNESS, 0.0])
        assert self.total(mesh, self.pressure_case()) == pytest.approx(expected, rel=1e-12)
        assert self.total(promote_to_tri6(mesh), self.pressure_case()) == pytest.approx(
            expected, rel=1e-12
        )

    @pytest.mark.parametrize("divisions", [(4, 1), (10, 3), (25, 7)])
    def test_a_force_totals_what_was_asked_for(self, divisions: tuple[int, int]) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, *divisions)
        case = uniaxial_case(PlaneState.STRESS)
        expected = np.array([PULL_N, 0.0])
        assert self.total(mesh, case) == pytest.approx(expected, rel=1e-12)
        assert self.total(promote_to_tri6(mesh), case) == pytest.approx(expected, rel=1e-12)

    def test_the_solved_stress_is_unchanged_by_refinement(self) -> None:
        # The consequence: a load that moved with the mesh would show up as a
        # stress that drifts with refinement and looks like convergence.
        coarse = PlaneSolver().solve(
            rectangle_mesh(LENGTH, HEIGHT, 4, 1), uniaxial_case(PlaneState.STRESS)
        )
        fine = PlaneSolver().solve(
            rectangle_mesh(LENGTH, HEIGHT, 25, 7), uniaxial_case(PlaneState.STRESS)
        )
        assert coarse.result.max_von_mises_mpa == pytest.approx(
            fine.result.max_von_mises_mpa, rel=1e-9
        )


@pytest.fixture(scope="module")
def one_edge() -> TriMesh:
    """nx=2, ny=1 leaves the loaded right-hand edge as a single element edge, so
    the three shares are readable without any sharing between neighbours."""
    return promote_to_tri6(rectangle_mesh(LENGTH, HEIGHT, 2, 1))


class TestTheQuadraticEdgeWeighting:
    """A 3-node line's shape functions integrate to L/6 at each corner and 2L/3
    at the midside. **This is not the tri6 face case**, where the corner
    functions integrate to exactly zero and the whole load sits on the midsides;
    carrying that rule across is statically equivalent and changes the peak
    stress next to the loaded edge without changing the resultant."""

    def shares(self, mesh: TriMesh) -> np.ndarray:
        forces, _ = assemble_loads(mesh, uniaxial_case(PlaneState.STRESS), 1.0)
        loaded = np.flatnonzero(np.isclose(mesh.nodes[:, 0], LENGTH))
        return forces.reshape(-1, 2)[loaded, 0] / PULL_N

    def test_the_corners_take_a_sixth_and_the_midside_two_thirds(
        self, one_edge: TriMesh
    ) -> None:
        assert sorted(self.shares(one_edge)) == pytest.approx(
            [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0]
        )

    def test_the_corner_share_is_not_zero(self, one_edge: TriMesh) -> None:
        # The specific wrong answer this guards: the tri6 *face* rule, which
        # puts nothing on the corners.
        assert float(self.shares(one_edge).min()) > 0.0

    def test_a_linear_edge_splits_in_half(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 2, 1)
        assert sorted(self.shares(mesh)) == pytest.approx([0.5, 0.5])


# -- badly posed models ------------------------------------------------------


class TestBadlyPosedModels:
    def free_to_slide_case(self) -> PlaneCase:
        """Held in x only, so the strip can still slide in y -- and pulled with
        a y component, so the free mode has work done on it."""
        return PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"])],
            loads=[
                ForceLoad(
                    where=FaceSelector(axis="x", side="max"), force_n=(PULL_N, PULL_N, 0.0)
                )
            ],
        )

    def test_a_model_free_to_translate_is_refused(self) -> None:
        # SuperLU returns a finite meaningless vector for a singular system, so
        # what catches this is the equilibrium residual, not a NaN.
        with pytest.raises(SolverError, match="under-constrained"):
            PlaneSolver().solve(rectangle_mesh(LENGTH, HEIGHT, 6, 2), self.free_to_slide_case())

    def test_a_quadratic_model_free_to_translate_is_refused_too(self) -> None:
        mesh = promote_to_tri6(rectangle_mesh(LENGTH, HEIGHT, 6, 2))
        with pytest.raises(SolverError, match="under-constrained"):
            PlaneSolver().solve(mesh, self.free_to_slide_case())

    def test_a_free_mode_the_load_never_excites_is_not_detected(self) -> None:
        """A recorded limitation, not a desired behaviour.

        The check is the equilibrium residual, and it is exact about what it
        checks: `K u = f` has a solution whenever `f` is orthogonal to the null
        space, however large that null space is. Restrain the strip in x only
        and pull it in x only, and the free y-translation has no load in it --
        SuperLU picks one member of the solution family and the residual is
        1e-10 of the applied load. The stress field is still right (a rigid-body
        mode carries no strain); `max_displacement_mm` is not, because the
        displacement it reports has an arbitrary offset in y.

        `app/solve/linear_static.py` has the same property and the same check.
        Detecting it needs a rank or null-space test, which neither solver does.
        This test exists so the boundary is written down rather than discovered.
        """
        mesh = rectangle_mesh(LENGTH, HEIGHT, 6, 2)
        case = PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"])],
            loads=[
                ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(PULL_N, 0.0, 0.0))
            ],
        )
        output = PlaneSolver().solve(mesh, case)
        assert output.nodal_stress is not None
        assert output.nodal_stress[:, 0] == pytest.approx(SIGMA, rel=1e-9)

    def test_the_equilibrium_residual_rejects_a_vector_that_does_not_solve(self) -> None:
        # The mechanism itself, pinned directly: a finite vector that does not
        # satisfy K u = f is refused. Without this the guard above could be
        # passing on SuperLU's rank warning alone.
        from app.solve.plane import _residual_is_small

        k = __import__("scipy.sparse", fromlist=["csc_matrix"]).csc_matrix(
            np.array([[2.0, 0.0], [0.0, 3.0]])
        )
        applied = np.array([2.0, 3.0])
        assert _residual_is_small(k, np.array([1.0, 1.0]), applied)
        assert not _residual_is_small(k, np.array([1.0, 1.5]), applied)

    def test_a_fully_fixed_model_is_refused(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 2, 1)
        case = PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[
                Fixture(
                    where=BoxSelector(min=(-1.0, -1.0, -1.0), max=(200.0, 200.0, 1.0)),
                    kind="clamp",
                )
            ],
            loads=[
                ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(1.0, 0.0, 0.0))
            ],
        )
        with pytest.raises(SolverError, match="nothing to solve"):
            PlaneSolver().solve(mesh, case)

    def test_an_interior_load_selection_warns_about_the_equal_split(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 10, 4)
        case = PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
            loads=[
                ForceLoad(
                    where=BoxSelector(min=(40.0, 8.0, -1.0), max=(60.0, 12.0, 1.0)),
                    force_n=(100.0, 0.0, 0.0),
                    name="Interior pull",
                )
            ],
        )
        output = PlaneSolver().solve(mesh, case)
        assert any("equally between its nodes" in w for w in output.result.warnings)

    def test_a_pressure_on_an_interior_selection_is_refused(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 10, 4)
        case = PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
            loads=[
                PressureLoad(
                    where=BoxSelector(min=(40.0, 8.0, -1.0), max=(60.0, 12.0, 1.0)),
                    pressure_mpa=1.0,
                )
            ],
        )
        with pytest.raises(SolverError, match="no complete boundary edges"):
            PlaneSolver().solve(mesh, case)

    def test_a_load_type_with_no_plane_form_is_refused_by_name(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 4, 2)
        case = PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
            loads=[GravityLoad()],
        )
        with pytest.raises(SolverError, match="GravityLoad cannot be applied"):
            PlaneSolver().solve(mesh, case)

    def test_an_out_of_plane_force_component_is_refused(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 4, 2)
        case = PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
            loads=[
                ForceLoad(
                    where=FaceSelector(axis="x", side="max"),
                    force_n=(0.0, 0.0, 100.0),
                    name="push",
                )
            ],
        )
        with pytest.raises(SolverError, match="out-of-plane component"):
            PlaneSolver().solve(mesh, case)

    def test_a_mesh_with_mixed_winding_is_refused(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 4, 2)
        mesh.tris[0] = mesh.tris[0][::-1]  # one inverted triangle
        with pytest.raises(SolverError, match="clockwise and anticlockwise"):
            PlaneSolver().solve(mesh, uniaxial_case(PlaneState.STRESS))


class TestAFixtureThatNamesZ:
    """A plane model has two displacement components per node. `dofs=["z"]` on
    one is somebody carrying a 3-D symmetry plane across, and dropping it
    silently would leave them free in a direction they believe they held."""

    def test_it_is_refused_by_name(self) -> None:
        fixture = Fixture(
            where=FaceSelector(axis="x", side="min"), dofs=["z"], name="symmetry"
        )
        with pytest.raises(SolverError, match="out-of-plane degree of freedom"):
            in_plane_dofs(fixture)

    def test_the_message_names_the_fixture_and_says_what_to_write(self) -> None:
        fixture = Fixture(
            where=FaceSelector(axis="x", side="min"), dofs=["x", "z"], name="mid-plane"
        )
        with pytest.raises(SolverError) as raised:
            in_plane_dofs(fixture)
        message = str(raised.value)
        assert "'mid-plane'" in message
        assert "kind='clamp'" in message

    def test_a_roller_normal_to_z_is_refused_too(self) -> None:
        # It holds only z, so accepting it would leave the model with no
        # restraint at all rather than with one too few.
        fixture = Fixture(where=FaceSelector(axis="x", side="min"), kind="roller", normal="z")
        with pytest.raises(SolverError, match="out-of-plane degree of freedom"):
            in_plane_dofs(fixture)

    def test_a_clamp_is_the_exception_and_holds_x_and_y(self) -> None:
        # `kind="clamp"` does not name z: it names "built in", and
        # `Fixture._resolve_dofs` expanded that into the 3-D spelling long
        # before this module saw it.
        fixture = Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")
        assert in_plane_dofs(fixture) == ["x", "y"]

    def test_a_clamped_edge_actually_holds_the_model(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 6, 2)
        case = PlaneCase(
            material=STEEL,
            thickness_mm=THICKNESS,
            state=PlaneState.STRESS,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
            loads=[
                ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(PULL_N, 0.0, 0.0))
            ],
        )
        output = PlaneSolver().solve(mesh, case)
        assert output.result.max_displacement_mm > 0.0


# -- patch tests -------------------------------------------------------------


def nodal_field(mesh: TriMesh, field) -> np.ndarray:  # type: ignore[no-untyped-def]
    """The flat DOF vector of a displacement field sampled at every node."""
    values = np.array([field(x, y) for x, y in mesh.nodes[:, :2]], dtype=np.float64)
    return values.ravel()


def patch_case(state: PlaneState = PlaneState.STRESS) -> PlaneCase:
    """A case carrying only the material, state and thickness a patch test needs.

    The fixtures and loads are required by the schema and unused: a patch test
    imposes its field directly and asks what the assembled operator does with
    it, which is the whole point -- it measures the element, not a boundary
    value problem.
    """
    return PlaneCase(
        material=STEEL,
        thickness_mm=1.0,
        state=state,
        fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
        loads=[ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(0.0, 0.0, 0.0))],
    )


def interior_nodes(mesh: TriMesh) -> np.ndarray:
    on_boundary = np.zeros(mesh.node_count, dtype=bool)
    on_boundary[mesh.boundary_edges.ravel()] = True
    midsides = mesh.boundary_edge_midsides
    if midsides is not None:
        on_boundary[midsides] = True
    return np.flatnonzero(~on_boundary)


class TestTheConstantStrainPatchTest:
    """A tri3 reproduces a linear displacement field exactly, so the assembled
    internal force at every interior node is zero: the element is in
    equilibrium under a uniform stress with no body force."""

    def test_a_linear_field_leaves_no_residual_at_an_interior_node(self) -> None:
        mesh = rectangle_mesh(30.0, 20.0, 6, 4)
        stiffness = assemble_stiffness(mesh, STEEL, PlaneState.STRESS, 1.0)
        displacement = nodal_field(
            mesh, lambda x, y: (1e-3 * x + 4e-4 * y, -7e-4 * x + 2e-3 * y)
        )
        residual = (stiffness @ displacement).reshape(-1, 2)

        inside = interior_nodes(mesh)
        assert len(inside) > 0
        scale = float(np.linalg.norm(residual, axis=1).max())
        assert float(np.linalg.norm(residual[inside], axis=1).max()) < 1e-9 * scale


class TestTheQuadraticPatchTest:
    """A tri6 can represent a quadratic displacement field exactly, so on a
    field the element contains *and* that is in equilibrium with no body force,
    it must reproduce both the stress and the equilibrium exactly.

    The field is pure bending about the x axis:

        u_x = -c x y,   u_y = c (x^2 + nu y^2) / 2

    which gives `eps = (-c y, nu c y, 0)`, hence `SXX = -E c y`, `SYY = 0`,
    `SXY = 0` in plane stress -- and `div sigma = 0`, so no body force is
    needed. That last property is what makes the residual test below meaningful
    rather than a statement about an integration rule.
    """

    curvature = 1e-5  # per mm

    def field(self, x: float, y: float) -> tuple[float, float]:
        c = self.curvature
        return (-c * x * y, 0.5 * c * (x * x + NU * y * y))

    def test_the_element_reproduces_the_bending_stress_exactly(self) -> None:
        mesh = promote_to_tri6(rectangle_mesh(30.0, 20.0, 3, 2))
        displacement = nodal_field(mesh, self.field)
        solver = PlaneSolver()
        stress = solver._stress_at(mesh, patch_case(), displacement, (1.0 / 3.0, 1.0 / 3.0))

        centroids = mesh.nodes[mesh.tris][:, :, :2].mean(axis=1)
        expected_sxx = -E * self.curvature * centroids[:, 1]
        assert stress[:, 0] == pytest.approx(expected_sxx, rel=1e-9, abs=1e-12)
        assert stress[:, 1] == pytest.approx(0.0, abs=1e-9)
        assert stress[:, 3] == pytest.approx(0.0, abs=1e-9)

    def test_the_nodal_stress_recovers_the_field_at_every_node(self) -> None:
        # Evaluated at each node's own natural coordinate, which is the point:
        # averaging centroid values would flatten the linear variation and
        # under-read the extreme fibre.
        mesh = promote_to_tri6(rectangle_mesh(30.0, 20.0, 3, 2))
        displacement = nodal_field(mesh, self.field)
        nodal = PlaneSolver()._recover_nodal_stress(mesh, patch_case(), displacement)
        expected = -E * self.curvature * mesh.nodes[:, 1]
        assert nodal[:, 0] == pytest.approx(expected, rel=1e-9, abs=1e-9)

    def test_the_equilibrated_field_leaves_no_residual_at_an_interior_node(self) -> None:
        mesh = promote_to_tri6(rectangle_mesh(30.0, 20.0, 6, 4))
        stiffness = assemble_stiffness(mesh, STEEL, PlaneState.STRESS, 1.0)
        residual = (stiffness @ nodal_field(mesh, self.field)).reshape(-1, 2)

        inside = interior_nodes(mesh)
        assert len(inside) > 0
        scale = float(np.linalg.norm(residual, axis=1).max())
        assert float(np.linalg.norm(residual[inside], axis=1).max()) < 1e-9 * scale


# -- the constitutive matrices ----------------------------------------------


class TestTheConstitutiveMatrices:
    def test_both_states_share_the_same_shear_modulus(self) -> None:
        g = E / (2.0 * (1.0 + NU))
        assert plane_stress_matrix(STEEL)[2, 2] == pytest.approx(g)
        assert plane_strain_matrix(STEEL)[2, 2] == pytest.approx(g)

    def test_they_are_symmetric(self) -> None:
        for d in (plane_stress_matrix(STEEL), plane_strain_matrix(STEEL)):
            assert d == pytest.approx(d.T)

    def test_plane_strain_is_the_stiffer_of_the_two(self) -> None:
        # An out-of-plane restraint can only add stiffness, never remove it.
        assert plane_strain_matrix(STEEL)[0, 0] > plane_stress_matrix(STEEL)[0, 0]

    def test_the_dispatch_returns_the_matrix_the_state_names(self) -> None:
        assert constitutive_matrix(STEEL, PlaneState.STRESS) == pytest.approx(
            plane_stress_matrix(STEEL)
        )
        assert constitutive_matrix(STEEL, PlaneState.STRAIN) == pytest.approx(
            plane_strain_matrix(STEEL)
        )

    def test_an_incompressible_material_is_refused_by_the_material_schema(self) -> None:
        # nu = 0.5 divides by zero in the plane-strain matrix. `Material`
        # already refuses it at the boundary, which is the right place -- this
        # asserts the plane path inherits that protection rather than needing
        # its own.
        with pytest.raises(ValueError):
            Material(
                name="rubber",
                youngs_modulus_mpa=10.0,
                poissons_ratio=0.5,
                yield_strength_mpa=5.0,
                density_kg_m3=1000.0,
            )


# -- thermal -----------------------------------------------------------------


def restrained_square_case(state: PlaneState, delta_t_k: float) -> PlaneCase:
    """A square built in on all four edges and heated uniformly.

    `u = 0` everywhere is the exact solution -- the thermal load vanishes at
    every interior node, because the integral of a shape function's gradient
    over its own patch is zero -- so the stress is exactly `-D eps_thermal`.
    The zero force load is there because the schema requires one; a plane model
    heated and otherwise untouched is a real case.
    """
    return PlaneCase(
        name="restrained heating",
        material=STEEL,
        thickness_mm=2.0,
        state=state,
        fixtures=[
            Fixture(where=FaceSelector(axis=axis, side=side), kind="clamp")
            for axis in ("x", "y")
            for side in ("min", "max")
        ],
        loads=[
            ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(0.0, 0.0, 0.0))
        ],
        delta_t_k=delta_t_k,
    )


class TestRestrainedThermalExpansion:
    delta_t = 80.0

    def test_plane_stress_gives_minus_e_alpha_dt_over_one_minus_nu(self) -> None:
        mesh = promote_to_tri6(rectangle_mesh(20.0, 20.0, 4, 4))
        output = PlaneSolver().solve(
            mesh, restrained_square_case(PlaneState.STRESS, self.delta_t)
        )
        alpha = STEEL.thermal_expansion_per_k
        assert alpha is not None
        expected = -E * alpha * self.delta_t / (1.0 - NU)
        assert output.nodal_stress is not None
        assert output.nodal_stress[:, 0] == pytest.approx(expected, rel=1e-9)
        assert output.nodal_stress[:, 2] == pytest.approx(0.0, abs=1e-9)

    def test_plane_strain_gives_minus_e_alpha_dt_over_one_minus_two_nu(self) -> None:
        # The (1 + nu) factor in the plane-strain thermal strain is what makes
        # this come out at 1/(1 - 2 nu) rather than 1/(1 - nu); dropping it
        # understates a restrained thermal stress by 30% for steel.
        mesh = promote_to_tri6(rectangle_mesh(20.0, 20.0, 4, 4))
        output = PlaneSolver().solve(
            mesh, restrained_square_case(PlaneState.STRAIN, self.delta_t)
        )
        alpha = STEEL.thermal_expansion_per_k
        assert alpha is not None
        expected = -E * alpha * self.delta_t / (1.0 - 2.0 * NU)
        assert output.nodal_stress is not None
        assert output.nodal_stress[:, 0] == pytest.approx(expected, rel=1e-9)
        # A fully restrained isotropic slice is hydrostatic: SZZ equals SXX.
        assert output.nodal_stress[:, 2] == pytest.approx(expected, rel=1e-9)

    def test_a_material_with_no_expansion_coefficient_is_refused_by_name(self) -> None:
        bare = Material(
            name="unknown alloy",
            youngs_modulus_mpa=70_000.0,
            poissons_ratio=0.33,
            yield_strength_mpa=200.0,
            density_kg_m3=2700.0,
        )
        case = restrained_square_case(PlaneState.STRESS, 50.0).model_copy(
            update={"material": bare}
        )
        mesh = rectangle_mesh(20.0, 20.0, 3, 3)
        with pytest.raises(SolverError, match="thermal_expansion_per_k"):
            PlaneSolver().solve(mesh, case)


# -- the seam ----------------------------------------------------------------


class TestPlanarSolverIsASibling:
    """`PlanarSolver` is not a `Solver`, on purpose. The two take different
    meshes and different cases, so a union-typed `solve` would push a branch
    into every caller -- the same argument `ModalSolver` is a sibling for."""

    def test_it_is_not_a_subclass_of_solver(self) -> None:
        assert not issubclass(PlanarSolver, Solver)
        assert not issubclass(PlaneSolver, Solver)

    def test_the_solver_implements_the_planar_seam(self) -> None:
        assert issubclass(PlaneSolver, PlanarSolver)
        assert PlaneSolver().name == "plane"

    def test_it_returns_the_shared_output_type(self) -> None:
        # Shared deliberately: the verification machinery, the provenance record
        # and the viewer all read `SolveOutput`, and a plane result is the same
        # four fields.
        mesh = rectangle_mesh(LENGTH, HEIGHT, 4, 2)
        output = PlaneSolver().solve(mesh, uniaxial_case(PlaneState.STRESS))
        assert output.displacements.shape == (mesh.node_count, 3)
        assert output.von_mises.shape == (mesh.element_count,)
        assert output.nodal_stress is not None
        assert output.nodal_stress.shape == (mesh.node_count, 6)
        # An in-plane problem has no out-of-plane shear -- that is the
        # assumption that makes it two-dimensional.
        assert output.nodal_stress[:, 4] == pytest.approx(0.0, abs=0.0)
        assert output.nodal_stress[:, 5] == pytest.approx(0.0, abs=0.0)
