"""Steady-state conduction verification.

Every temperature in this file is checked against a closed form -- a linear
profile, a logarithmic one, a Biot-number tip temperature -- and never against a
previously recorded solver output. A golden file would record whatever the
assembly happens to produce, including a film coefficient that is wrong by a
million because it never left W/(m^2 K).

The conductivities, film coefficients and fluxes here are **inputs**, not
transcribed material properties: the closed forms hold for any positive value,
and nothing in this file claims a sourced number for a real material. The one
place a real material appears is the coupling section, which uses the register's
own steel because the expansion coefficient there is transcribed with a citation.
"""

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.mesh.types import TetMesh
from app.observe import collect
from app.solve.base import ConductionSolver, ModalSolver, Solver
from app.solve.conduction import (
    _T3_LOAD_UNIT,
    _T3_MASS_UNIT,
    _T6_LOAD_UNIT,
    _T6_MASS_UNIT,
    CONDUCTIVITY_W_MK_TO_W_MMK,
    Convection,
    FixedTemperature,
    HeatFlux,
    SteadyConductionSolver,
    ThermalCase,
    _barycentric_integral,
    _facet_integrals,
    convecting_bar_tip_temperature_k,
    element_temperature_change,
    hollow_cylinder_temperature_k,
    thermal_load_from_field,
    thermal_stress_correction_field,
    volumetric_source_load,
)
from app.solve.linear_static import constitutive_matrix
from app.solve.materials import MATERIALS
from app.solve.registry import INTERNAL, build_conduction_solver
from app.solve.selection import distribute_force, select_nodes
from app.solve.thermal import thermal_load, thermal_stress_correction
from app.solve.types import (
    BoxSelector,
    CylinderSelector,
    FaceSelector,
    Material,
    SolverError,
)

STEEL = MATERIALS["steel-1018"]

#: A conductivity to solve with. An input to the closed forms below, not a
#: property claim -- see the module docstring.
CONDUCTIVITY_W_MK = 50.0

BAR_SIZE = (10.0, 10.0, 100.0)
BAR_LENGTH_MM = BAR_SIZE[2]
BAR_AREA_MM2 = BAR_SIZE[0] * BAR_SIZE[1]


def bar(divisions: tuple[int, int, int] = (2, 2, 8), quadratic: bool = False) -> TetMesh:
    """A prismatic bar along +z. Its four lateral faces are left insulated."""
    mesh = box_mesh(BAR_SIZE, divisions=divisions)
    return promote_to_tet10(mesh) if quadratic else mesh


def annulus_mesh(
    inner_r: float,
    outer_r: float,
    height: float,
    sweep_rad: float,
    divisions: tuple[int, int, int],
) -> TetMesh:
    """A wedge of a hollow cylinder, meshed by mapping a structured box.

    The box is meshed in (r - ri, theta, z) and the nodes are then moved onto
    their cylindrical positions, so every node sits **exactly** on its intended
    radius and the inner and outer walls can be selected by radius with a tight
    tolerance. The map is orientation preserving for r > 0, so the windings the
    box mesher fixed stay fixed -- asserted below rather than assumed.

    A wedge rather than a full tube on purpose: the two radial cut faces and the
    two ends are insulated, and an insulated boundary is what the weak form does
    when nothing is written for it. That is exactly the axisymmetric problem the
    logarithmic profile solves, at a fraction of the elements.
    """
    box = box_mesh((outer_r - inner_r, sweep_rad, height), divisions=divisions)
    u, v, w = box.nodes.T
    radius = inner_r + u
    nodes = np.column_stack([radius * np.cos(v), radius * np.sin(v), w])
    return TetMesh(nodes=nodes, tets=box.tets)


def held_ends(hot_k: float, cold_k: float) -> list:
    return [
        FixedTemperature(where=FaceSelector(axis="z", side="min"), temperature_k=hot_k),
        FixedTemperature(where=FaceSelector(axis="z", side="max"), temperature_k=cold_k),
    ]


class TestTheSurfaceIntegralsAreExact:
    """The Robin and flux terms are surface integrals of shape functions.

    They are computed from the barycentric monomial formula rather than from a
    recalled quadrature table or a recalled 6x6 mass matrix, so the first thing
    to check is the formula itself against values anyone can do by hand.
    """

    def test_the_barycentric_formula_reproduces_the_hand_values(self) -> None:
        assert _barycentric_integral(0, 0, 0) == pytest.approx(1.0)  # unit area
        assert _barycentric_integral(1, 0, 0) == pytest.approx(1.0 / 3.0)
        assert _barycentric_integral(2, 0, 0) == pytest.approx(1.0 / 6.0)
        assert _barycentric_integral(1, 1, 0) == pytest.approx(1.0 / 12.0)

    def test_a_linear_face_puts_a_third_of_its_area_on_each_corner(self) -> None:
        assert _T3_LOAD_UNIT == pytest.approx([1 / 3, 1 / 3, 1 / 3])

    def test_a_quadratic_face_puts_nothing_on_its_corners(self) -> None:
        """The corner shape functions of a 6-node triangle integrate to zero.

        This is the same fact `selection.distribute_force` documents for a
        mechanical traction, and it is counter-intuitive enough that spreading
        the load over the corners instead is a standard mistake.
        """
        assert _T6_LOAD_UNIT[:3] == pytest.approx([0.0, 0.0, 0.0], abs=1e-15)
        assert _T6_LOAD_UNIT[3:] == pytest.approx([1 / 3, 1 / 3, 1 / 3])

    def test_the_face_integrals_sum_to_the_area(self) -> None:
        # Partition of unity: sum(N_i) == 1 everywhere, so the integrals sum to A.
        assert _T3_LOAD_UNIT.sum() == pytest.approx(1.0)
        assert _T6_LOAD_UNIT.sum() == pytest.approx(1.0)

    def test_the_mass_matrices_are_symmetric_and_sum_to_the_area(self) -> None:
        for mass in (_T3_MASS_UNIT, _T6_MASS_UNIT):
            assert mass == pytest.approx(mass.T)
            assert mass.sum() == pytest.approx(1.0)

    def test_each_mass_row_sums_to_that_shape_functions_integral(self) -> None:
        # sum_j integral N_i N_j = integral N_i * sum_j N_j = integral N_i.
        assert _T3_MASS_UNIT.sum(axis=1) == pytest.approx(_T3_LOAD_UNIT)
        assert _T6_MASS_UNIT.sum(axis=1) == pytest.approx(_T6_LOAD_UNIT)

    def test_a_corner_couples_only_to_the_midside_opposite_it(self) -> None:
        """The one asymmetric entry of the quadratic mass matrix, by hand.

        `integral N_c N_m dA` vanishes for the two midsides that touch corner c
        and is exactly -1/45 of the area for the third. Nothing about a *uniform*
        ambient temperature can see this -- the row sums are what a uniform film
        loads with, and they survive any relabelling of the three midsides -- so
        without it a permuted midside ordering assembles a wrong matrix that
        every physical test in this file still passes.
        """
        for corner, opposite in enumerate((4, 5, 3)):
            touching = [m for m in (3, 4, 5) if m != opposite]
            for midside in touching:
                assert _T6_MASS_UNIT[corner, midside] == pytest.approx(0.0, abs=1e-15)
            assert _T6_MASS_UNIT[corner, opposite] == pytest.approx(-1.0 / 45.0)

    def test_the_flux_weighting_agrees_with_the_mechanical_one(self) -> None:
        """A flux and a pressure named with one selector must load one region.

        `selection.distribute_force` normalises its weights and this module does
        not, so the two cannot share a function -- but they must agree on the
        shape, or a thermal boundary and a mechanical one would disagree about
        where a quadratic face carries its load.
        """
        mesh = bar(quadratic=True)
        nodes = select_nodes(mesh, FaceSelector(axis="z", side="max"))
        carriers, areas, load_unit, _ = _facet_integrals(mesh, nodes)

        ours = np.zeros(mesh.node_count)
        np.add.at(ours, carriers.ravel(), (areas[:, None] * load_unit[None, :]).ravel())

        theirs = np.zeros(mesh.node_count)
        share, warning = distribute_force(mesh, nodes, np.array([0.0, 0.0, 1.0]))
        theirs[nodes] = share[:, 2]

        assert warning is None
        assert ours[nodes] / ours[nodes].sum() == pytest.approx(theirs[nodes])


class TestTheQuadraticFaceNodeOrdering:
    def test_each_midside_sits_between_the_two_corners_it_is_written_for(self) -> None:
        """`_T6_SHAPES` assumes midside i lies between corner i and corner i+1.

        Getting that wrong permutes the mass matrix, which still solves and still
        looks plausible -- the film would simply be attached to the wrong three
        points of every face.
        """
        mesh = bar(quadratic=True)
        nodes = select_nodes(mesh, FaceSelector(axis="z", side="max"))
        carriers, _, _, _ = _facet_integrals(mesh, nodes)
        assert carriers.shape[1] == 6

        corners = mesh.nodes[carriers[:, :3]]
        midsides = mesh.nodes[carriers[:, 3:]]
        for local, (a, b) in enumerate(((0, 1), (1, 2), (2, 0))):
            expected = 0.5 * (corners[:, a] + corners[:, b])
            assert midsides[:, local] == pytest.approx(expected)


class TestABarHeldAtBothEnds:
    """T(x) = T0 + (T1 - T0) x / L.

    The exact solution is linear, so it lies inside both element spaces and the
    discrete answer is the exact one to solver precision -- not merely close.
    """

    HOT = 400.0
    COLD = 300.0

    def profile(self, mesh: TetMesh) -> np.ndarray:
        z = mesh.nodes[:, 2]
        return self.HOT + (self.COLD - self.HOT) * z / BAR_LENGTH_MM

    def solve(self, mesh: TetMesh, conductivity: float = CONDUCTIVITY_W_MK):
        case = ThermalCase(
            name="held bar",
            conductivity_w_mk=conductivity,
            boundaries=held_ends(self.HOT, self.COLD),
        )
        return SteadyConductionSolver().solve(mesh, case)

    def test_tet4_reproduces_the_linear_profile(self) -> None:
        mesh = bar()
        field = self.solve(mesh)
        assert field.temperatures_k == pytest.approx(self.profile(mesh), abs=1e-9)

    def test_tet10_reproduces_the_linear_profile(self) -> None:
        mesh = bar(quadratic=True)
        field = self.solve(mesh)
        assert field.temperatures_k == pytest.approx(self.profile(mesh), abs=1e-9)

    def test_the_two_element_orders_agree_at_the_shared_nodes(self) -> None:
        """`promote_to_tet10` keeps the corner nodes in place and in order, so
        the first `n` entries of the two fields describe the same points."""
        linear = bar()
        quadratic = promote_to_tet10(linear)
        assert self.solve(quadratic).temperatures_k[: linear.node_count] == pytest.approx(
            self.solve(linear).temperatures_k, abs=1e-9
        )

    def test_conductivity_does_not_change_the_profile(self) -> None:
        """It scales the flux, not the shape.

        A pure-Dirichlet steady profile is invariant to `k` because `k` multiplies
        the whole conductivity matrix, so it cancels out of `K T = 0`. That makes
        this a cheap, sharp check that the conductivity really does multiply
        everything and is not, say, applied to the tet10 Gauss loop only.
        """
        mesh = bar()
        cold = self.solve(mesh, conductivity=0.2).temperatures_k
        hot = self.solve(mesh, conductivity=400.0).temperatures_k
        assert cold == pytest.approx(hot, abs=1e-9)

    def test_the_heat_flux_is_fouriers_law(self) -> None:
        """q = -k dT/dx, and this is what pins the conductivity conversion.

        dT/dz is -1 K/mm, so in the units the caller works in the flux is
        k * 1000 W/m^2 down the bar. Nothing about the *temperature* field can
        see the absolute size of `CONDUCTIVITY_W_MK_TO_W_MMK`; this can.
        """
        field = self.solve(bar())
        expected = CONDUCTIVITY_W_MK * 1000.0
        assert field.heat_flux_w_m2[:, 2] == pytest.approx(expected, rel=1e-9)
        assert field.heat_flux_w_m2[:, :2] == pytest.approx(0.0, abs=1e-6)

    def test_the_held_ends_supply_and_remove_the_same_heat(self) -> None:
        """Steady state stores nothing, so the net across both held faces is zero."""
        field = self.solve(bar())
        scale = CONDUCTIVITY_W_MK * BAR_AREA_MM2 * 1e-3  # the heat each end carries, W
        assert abs(field.result.fixed_temperature_heat_w) < 1e-9 * scale

    def test_both_ends_held_together_gives_a_uniform_field_and_no_flux(self) -> None:
        mesh = bar()
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=held_ends(350.0, 350.0),
        )
        field = SteadyConductionSolver().solve(mesh, case)
        assert field.temperatures_k == pytest.approx(350.0, abs=1e-9)
        # Against the k * 1000 W/m^2 this bar carries for a 1 K/mm gradient, so
        # this is thirteen orders down -- solver round-off, not a gradient.
        assert field.heat_flux_w_m2 == pytest.approx(0.0, abs=1e-6)

    def test_an_exactly_uniform_field_has_exactly_zero_flux(self) -> None:
        """The arithmetic statement, separated from the solved one on purpose.

        A *solved* uniform field is uniform to solver precision, and dividing its
        last few bits by a millimetre is what the previous test tolerates. This
        one hands the gradient operator a field that is uniform bit for bit, so
        the only acceptable answer is exactly zero.
        """
        for mesh in (bar(), bar(quadratic=True)):
            uniform = np.full(mesh.node_count, 350.0)
            flux = SteadyConductionSolver.heat_flux(mesh, uniform, CONDUCTIVITY_W_MK * 1e-3)
            assert not flux.any()


class TestAHollowCylinder:
    """T(r) = Ti + (To - Ti) ln(r/ri) / ln(ro/ri).

    Curved, so unlike the bar it is *not* in the element space and the answer
    converges rather than being exact. That is the point: a solver that only ever
    reproduces the fields it can represent exactly has not been tested on
    anything.
    """

    INNER_R = 20.0
    OUTER_R = 60.0
    INNER_T = 500.0
    OUTER_T = 300.0
    SWEEP = np.pi / 6.0
    #: One element thick, and thin, so that the wedge is effectively planar. Both
    #: flat faces are insulated, so the exact solution does not vary along z --
    #: but a single tall element does not *represent* that well, and the Kuhn
    #: decomposition is not symmetric, so a 10 mm slab spreads a 0.43 K
    #: circumferential wobble across each ring that radial refinement cannot
    #: remove. Measured, and the reason this number is 1 and not 10.
    HEIGHT = 1.0

    def solve(self, radial: int, quadratic: bool = False):
        # Refined circumferentially as well as radially: holding one direction
        # fixed puts a floor under the error and the study then measures the
        # floor rather than the method.
        mesh = annulus_mesh(
            self.INNER_R, self.OUTER_R, self.HEIGHT, self.SWEEP, divisions=(radial, radial, 1)
        )
        if quadratic:
            mesh = promote_to_tet10(mesh)
        spacing = (self.OUTER_R - self.INNER_R) / radial
        case = ThermalCase(
            name="tube wall",
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                FixedTemperature(
                    where=CylinderSelector(
                        axis_point=(0.0, 0.0, 0.0),
                        axis_direction=(0.0, 0.0, 1.0),
                        radius=self.INNER_R,
                        radius_tolerance=0.25 * spacing,
                    ),
                    temperature_k=self.INNER_T,
                ),
                FixedTemperature(
                    where=CylinderSelector(
                        axis_point=(0.0, 0.0, 0.0),
                        axis_direction=(0.0, 0.0, 1.0),
                        radius=self.OUTER_R,
                        radius_tolerance=0.25 * spacing,
                    ),
                    temperature_k=self.OUTER_T,
                ),
            ],
        )
        return mesh, SteadyConductionSolver().solve(mesh, case)

    def exact(self, mesh: TetMesh) -> np.ndarray:
        radius = np.linalg.norm(mesh.nodes[:, :2], axis=1)
        return hollow_cylinder_temperature_k(
            radius, self.INNER_R, self.OUTER_R, self.INNER_T, self.OUTER_T
        )

    def error(self, radial: int, quadratic: bool = False) -> float:
        mesh, field = self.solve(radial, quadratic=quadratic)
        return float(np.abs(field.temperatures_k - self.exact(mesh)).max())

    def test_the_mapped_mesh_is_not_inverted(self) -> None:
        mesh = annulus_mesh(self.INNER_R, self.OUTER_R, self.HEIGHT, self.SWEEP, (4, 4, 1))
        assert (mesh.signed_volumes() > 0).all()

    def test_the_profile_is_logarithmic(self) -> None:
        # Measured 0.121 K on an 8x8 wedge, against a 200 K span across the wall.
        assert self.error(8) < 0.25

    def test_a_linear_profile_would_not_pass_that_tolerance(self) -> None:
        """The tolerance has to be tight enough to tell the two profiles apart.

        A straight line between the same two wall temperatures is off by 26.9 K
        in the middle of this wall -- more than a hundred times the tolerance
        above -- so the test is measuring the logarithm and not merely the two
        boundary values, which any solver that assembled nothing at all would
        still reproduce.
        """
        mesh, _ = self.solve(8)
        radius = np.linalg.norm(mesh.nodes[:, :2], axis=1)
        straight = self.INNER_T + (self.OUTER_T - self.INNER_T) * (radius - self.INNER_R) / (
            self.OUTER_R - self.INNER_R
        )
        assert np.abs(straight - self.exact(mesh)).max() > 20.0

    def test_refining_the_mesh_converges_on_the_closed_form(self) -> None:
        errors = [self.error(radial) for radial in (4, 8, 16)]
        assert errors[0] > errors[1] > errors[2]
        # Measured: 0.419, 0.121, 0.0389 K, so orders 1.79 and 1.64.
        orders = [np.log2(errors[i] / errors[i + 1]) for i in range(2)]
        assert min(orders) > 1.4, f"observed orders {orders} from errors {errors}"

    def test_the_faceted_wall_and_not_the_element_order_is_the_limit(self) -> None:
        """tet10 converges at second order here, and that is the honest result.

        A quadratic element on a smooth solution should do better than second
        order, and on the bar it is exact. It is not here because the walls are
        **polygons**: `promote_to_tet10` puts midside nodes at the midpoints of
        straight edges, so the domain is still inscribed in the two circles and
        the geometric error is O(h^2) whatever the interpolation does. Measured
        orders 2.003 and 2.001 -- clean enough to be the geometry and nothing
        else -- and tet10 at 8 divisions (0.0975 K) is barely ahead of tet4 at
        the same count (0.121 K).

        Written down because the obvious reading of that pair of numbers is that
        the quadratic elements are broken, and they are not. Curved boundary
        geometry is what would move it, not a better element.
        """
        errors = [self.error(radial, quadratic=True) for radial in (2, 4, 8)]
        orders = [np.log2(errors[i] / errors[i + 1]) for i in range(2)]
        assert 1.8 < min(orders) and max(orders) < 2.3, (
            f"observed orders {orders} from errors {errors}"
        )


class TestAConvectionBoundary:
    """A bar held at one end and cooled by a film at the other.

    The closed form contains `h`, `k` and `L` together through the Biot number,
    which is what makes it the test that the Robin term is *assembled* -- a film
    accepted and then dropped, or one whose coefficient never left W/(m^2 K),
    both give a tip temperature that is still between base and ambient and still
    looks entirely reasonable.
    """

    BASE = 500.0
    AMBIENT = 300.0
    FILM = 250.0

    def case(self, film: float | None = None, base: float | None = None) -> ThermalCase:
        return ThermalCase(
            name="cooled bar",
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                FixedTemperature(
                    where=FaceSelector(axis="z", side="min"),
                    temperature_k=self.BASE if base is None else base,
                ),
                Convection(
                    where=FaceSelector(axis="z", side="max"),
                    film_coefficient_w_m2k=self.FILM if film is None else film,
                    ambient_temperature_k=self.AMBIENT,
                ),
            ],
        )

    def tip(self, field, mesh: TetMesh) -> float:
        far = np.flatnonzero(np.isclose(mesh.nodes[:, 2], BAR_LENGTH_MM))
        return float(field.temperatures_k[far].mean())

    def expected_tip(self, film: float | None = None) -> float:
        return convecting_bar_tip_temperature_k(
            base_temperature_k=self.BASE,
            ambient_temperature_k=self.AMBIENT,
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            film_coefficient_w_m2k=self.FILM if film is None else film,
            length_mm=BAR_LENGTH_MM,
        )

    def test_the_tip_temperature_matches_the_closed_form_on_tet4(self) -> None:
        mesh = bar()
        field = SteadyConductionSolver().solve(mesh, self.case())
        assert self.tip(field, mesh) == pytest.approx(self.expected_tip(), abs=1e-9)

    def test_the_tip_temperature_matches_the_closed_form_on_tet10(self) -> None:
        mesh = bar(quadratic=True)
        field = SteadyConductionSolver().solve(mesh, self.case())
        assert self.tip(field, mesh) == pytest.approx(self.expected_tip(), abs=1e-9)

    def test_the_whole_field_is_the_straight_line_between_base_and_tip(self) -> None:
        mesh = bar()
        field = SteadyConductionSolver().solve(mesh, self.case())
        tip = self.expected_tip()
        expected = self.BASE + (tip - self.BASE) * mesh.nodes[:, 2] / BAR_LENGTH_MM
        assert field.temperatures_k == pytest.approx(expected, abs=1e-9)

    def test_a_stronger_film_pulls_the_tip_towards_ambient(self) -> None:
        mesh = bar()
        tips = [
            self.tip(SteadyConductionSolver().solve(mesh, self.case(film=h)), mesh)
            for h in (10.0, 250.0, 100_000.0)
        ]
        assert tips[0] > tips[1] > tips[2] > self.AMBIENT
        # Each one still on its own closed form, so "monotone" is not the whole
        # claim -- the three answers are individually right.
        for tip, film in zip(tips, (10.0, 250.0, 100_000.0), strict=True):
            assert tip == pytest.approx(self.expected_tip(film), abs=1e-9)
        # A huge film approaches a prescribed temperature: Bi = 200 leaves the
        # tip 1.0 K above ambient, out of the 200 K it started above it.
        assert tips[2] - self.AMBIENT < 1.0

    def test_a_film_alone_is_a_well_posed_model(self) -> None:
        """No fixed temperature anywhere, and yet not floating: the Robin term
        sits on the diagonal and fixes the level by itself."""
        mesh = bar()
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                Convection(
                    where=FaceSelector(axis="z", side="max"),
                    film_coefficient_w_m2k=self.FILM,
                    ambient_temperature_k=self.AMBIENT,
                )
            ],
        )
        field = SteadyConductionSolver().solve(mesh, case)
        # Nothing else adds or removes heat, so the whole part settles at ambient.
        assert field.temperatures_k == pytest.approx(self.AMBIENT, abs=1e-9)
        assert field.result.fixed_temperature_heat_w == pytest.approx(0.0, abs=1e-12)

    def test_the_base_supplies_the_heat_the_film_removes(self) -> None:
        mesh = bar()
        field = SteadyConductionSolver().solve(mesh, self.case())
        # Q = h A (T_tip - T_inf), in W: h in W/(m^2 K), A in m^2.
        area_m2 = BAR_AREA_MM2 * 1e-6
        expected = self.FILM * area_m2 * (self.expected_tip() - self.AMBIENT)
        assert field.result.fixed_temperature_heat_w == pytest.approx(expected, rel=1e-9)


class TestAHeatFluxBoundary:
    """T(x) = T_base + q'' x / k, with the flux entering the far face.

    Linear again, so exact -- and it is the test that pins the flux conversion
    against the conductivity conversion, since `q''/k` is what appears.
    """

    BASE = 300.0
    FLUX_W_M2 = 1000.0

    def case(self) -> ThermalCase:
        return ThermalCase(
            name="heated bar",
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                FixedTemperature(
                    where=FaceSelector(axis="z", side="min"), temperature_k=self.BASE
                ),
                HeatFlux(
                    where=FaceSelector(axis="z", side="max"), flux_w_m2=self.FLUX_W_M2
                ),
            ],
        )

    def expected(self, mesh: TetMesh) -> np.ndarray:
        # q'' in W/m^2, z in mm, k in W/(m K)  =>  dT = q'' * z * 1e-3 / k.
        return self.BASE + self.FLUX_W_M2 * mesh.nodes[:, 2] * 1e-3 / CONDUCTIVITY_W_MK

    def test_the_profile_matches_the_closed_form(self) -> None:
        mesh = bar()
        field = SteadyConductionSolver().solve(mesh, self.case())
        assert field.temperatures_k == pytest.approx(self.expected(mesh), abs=1e-9)

    def test_the_same_on_tet10(self) -> None:
        mesh = bar(quadratic=True)
        field = SteadyConductionSolver().solve(mesh, self.case())
        assert field.temperatures_k == pytest.approx(self.expected(mesh), abs=1e-9)

    def test_the_applied_heat_is_the_flux_times_the_area(self) -> None:
        """Absolute, not a ratio: this is what pins `FLUX_W_M2_TO_W_MM2`.

        The held face must remove exactly what the flux puts in, so the reported
        fixed-temperature heat is minus q'' A.
        """
        field = SteadyConductionSolver().solve(bar(), self.case())
        expected = -self.FLUX_W_M2 * (BAR_AREA_MM2 * 1e-6)
        assert field.result.fixed_temperature_heat_w == pytest.approx(expected, rel=1e-9)

    def test_the_reported_flux_returns_to_the_units_it_came_in_as(self) -> None:
        field = SteadyConductionSolver().solve(bar(), self.case())
        # Heat enters at z = L and travels down the bar, so q_z is negative.
        assert field.heat_flux_w_m2[:, 2] == pytest.approx(-self.FLUX_W_M2, rel=1e-9)

    def test_refining_the_mesh_does_not_change_the_heat_applied(self) -> None:
        coarse = SteadyConductionSolver().solve(bar(divisions=(1, 1, 2)), self.case())
        fine = SteadyConductionSolver().solve(bar(divisions=(3, 3, 12)), self.case())
        assert coarse.result.fixed_temperature_heat_w == pytest.approx(
            fine.result.fixed_temperature_heat_w, rel=1e-9
        )

    def test_a_flux_on_interior_nodes_applies_nothing_and_says_so(self) -> None:
        """No equal-split fallback: a flux is per unit area and there is no area."""
        mesh = bar()
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                FixedTemperature(
                    where=FaceSelector(axis="z", side="min"), temperature_k=self.BASE
                ),
                HeatFlux(
                    where=BoxSelector(min=(4.0, 4.0, 40.0), max=(6.0, 6.0, 60.0)),
                    flux_w_m2=self.FLUX_W_M2,
                    name="inside the bar",
                ),
            ],
        )
        field = SteadyConductionSolver().solve(mesh, case)
        assert field.temperatures_k == pytest.approx(self.BASE, abs=1e-9)
        assert any("inside the bar" in w for w in field.result.warnings)
        assert any("needs a surface" in w for w in field.result.warnings)


class TestAVolumetricSource:
    def test_the_source_integrates_to_the_power_in_the_volume(self) -> None:
        """Partition of unity: the nodal heat sums to q_v * V, exactly.

        True for both element orders and independent of the mesh, which is the
        statement that the source is integrated rather than divided up by node
        count -- the latter agrees only on a uniform mesh.
        """
        for mesh in (bar(divisions=(2, 3, 5)), bar(divisions=(2, 3, 5), quadratic=True)):
            source_w_mm3 = 2.5e-4
            heat = volumetric_source_load(mesh, source_w_mm3)
            assert heat.sum() == pytest.approx(source_w_mm3 * mesh.volume, rel=1e-12)

    def test_no_source_assembles_nothing(self) -> None:
        assert not volumetric_source_load(bar(), 0.0).any()

    def test_a_linear_element_shares_a_quarter_of_each_tet_it_touches(self) -> None:
        """`integral N_i dV = V/4` for a tet4, so a node's share is a quarter of
        the tets it belongs to and nothing else.

        The total alone cannot see this: splitting `q_v V` equally between the
        nodes gives the same sum and a different part. A corner of the bar
        touches far fewer tets than a node in the middle, so the two are not
        remotely the same load.
        """
        mesh = bar(divisions=(2, 3, 5))
        source_w_mm3 = 2.5e-4
        heat = volumetric_source_load(mesh, source_w_mm3)

        volumes = np.abs(mesh.signed_volumes())
        expected = np.zeros(mesh.node_count)
        np.add.at(expected, mesh.tets.ravel(), np.repeat(volumes / 4.0, 4))
        assert heat == pytest.approx(source_w_mm3 * expected, rel=1e-12)
        assert heat.min() < 0.25 * heat.max()  # a corner against an interior node

    def test_a_quadratic_element_gives_its_corners_negative_heat(self) -> None:
        """`integral N_corner dV` is **-V/20** for a tet10, and `V/5` at a midside.

        Negative, and it is not a mistake: the corner shape function of a
        quadratic element is negative over most of the element. The two sum to
        `4(-1/20) + 6(1/5) = 1`, so the total is still `q_v V` -- which is
        exactly why a total-only check cannot tell this apart from an equal
        split, and why the sign is asserted here.
        """
        mesh = bar(divisions=(2, 2, 4), quadratic=True)
        assert mesh.midside is not None
        heat = volumetric_source_load(mesh, 1.0)
        corners = np.unique(mesh.tets)
        midsides = np.unique(mesh.midside)
        assert (heat[corners] < 0.0).all()
        assert (heat[midsides] > 0.0).all()
        assert heat.sum() == pytest.approx(mesh.volume, rel=1e-12)

    def test_a_heated_bar_removes_its_generated_heat_through_the_held_ends(self) -> None:
        mesh = bar()
        source_w_m3 = 5.0e5
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=held_ends(300.0, 300.0),
            volumetric_source_w_m3=source_w_m3,
        )
        field = SteadyConductionSolver().solve(mesh, case)
        volume_m3 = mesh.volume * 1e-9
        assert field.result.fixed_temperature_heat_w == pytest.approx(
            -source_w_m3 * volume_m3, rel=1e-9
        )
        # And it must actually get hotter in the middle than at its held ends.
        assert field.temperatures_k.max() > 300.0


class TestAFloatingModelIsRefused:
    """The guard this file exists for.

    A model with nothing setting its temperature level is singular, and SuperLU
    answers a singular system with a finite, meaningless vector -- so this is
    caught structurally and by a heat balance, never by looking for NaNs.
    """

    def test_a_model_with_only_a_flux_is_refused_by_name(self) -> None:
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                HeatFlux(where=FaceSelector(axis="z", side="max"), flux_w_m2=1000.0)
            ],
        )
        with pytest.raises(SolverError) as raised:
            SteadyConductionSolver().solve(bar(), case)
        message = str(raised.value)
        assert "thermally floating" in message
        assert "fixed_temperature" in message
        assert "convection" in message
        # The *detail* matters, not just the family: this case is refused before
        # anything is assembled, because a case declaring neither boundary type
        # cannot be anything but floating and a large mesh should not be built to
        # find that out. The downstream check has its own wording, so pinning
        # this one is what tells the two apart.
        assert "carries only a heat flux" in message

    def test_the_refusal_says_what_to_add(self) -> None:
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[HeatFlux(where=FaceSelector(axis="z", side="max"), flux_w_m2=0.0)],
        )
        with pytest.raises(SolverError, match="film coefficient and an ambient temperature"):
            SteadyConductionSolver().solve(bar(), case)

    def test_a_film_that_selected_no_facets_is_refused_not_solved(self) -> None:
        """A declared film is not an assembled one.

        The structural check sees a `Convection` and lets the case through; the
        film then lands on interior nodes, contributes no matrix term, and the
        model is floating after all. Catching this at the point where it is known
        to have contributed nothing is deterministic, where relying on the
        factorisation to notice a singular matrix is not.
        """
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                Convection(
                    where=BoxSelector(min=(4.0, 4.0, 40.0), max=(6.0, 6.0, 60.0)),
                    film_coefficient_w_m2k=25.0,
                    ambient_temperature_k=300.0,
                    name="film on nothing",
                )
            ],
        )
        with pytest.raises(SolverError, match="thermally floating"):
            SteadyConductionSolver().solve(bar(), case)

    def test_a_model_with_every_node_held_is_refused(self) -> None:
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                FixedTemperature(
                    where=BoxSelector(min=(-1.0, -1.0, -1.0), max=(11.0, 11.0, 101.0)),
                    temperature_k=350.0,
                )
            ],
        )
        with pytest.raises(SolverError, match="nothing to solve"):
            SteadyConductionSolver().solve(bar(), case)


class TestOverlappingFixedTemperatures:
    def test_two_boundaries_disagreeing_on_one_node_are_refused(self) -> None:
        """Selectors carry tolerance bands, so an overlap is easy to write.

        Keeping whichever came last would put a step in the boundary data that
        the solve then smooths into something that looks like an answer.
        """
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                FixedTemperature(
                    where=FaceSelector(axis="z", side="min"),
                    temperature_k=400.0,
                    name="held cold end",
                ),
                FixedTemperature(
                    where=BoxSelector(min=(-1.0, -1.0, -1.0), max=(11.0, 11.0, 1.0)),
                    temperature_k=500.0,
                    name="the heater pad",
                ),
            ],
        )
        with pytest.raises(SolverError) as raised:
            SteadyConductionSolver().solve(bar(), case)
        message = str(raised.value)
        assert "held cold end" in message and "the heater pad" in message
        assert "tighten one selector" in message

    def test_two_boundaries_that_agree_are_accepted(self) -> None:
        case = ThermalCase(
            conductivity_w_mk=CONDUCTIVITY_W_MK,
            boundaries=[
                FixedTemperature(
                    where=FaceSelector(axis="z", side="min"), temperature_k=400.0
                ),
                FixedTemperature(
                    where=BoxSelector(min=(-1.0, -1.0, -1.0), max=(11.0, 11.0, 1.0)),
                    temperature_k=400.0,
                ),
                FixedTemperature(
                    where=FaceSelector(axis="z", side="max"), temperature_k=300.0
                ),
            ],
        )
        field = SteadyConductionSolver().solve(bar(), case)
        assert field.result.max_temperature_k == pytest.approx(400.0)


class TestTheCouplingToStress:
    """A computed field driving thermal strain.

    Not wired into `LinearStaticSolver` -- see the seam named in the module
    docstring -- so what can be proved here is that the field path is a strict
    generalisation of the uniform path, which is itself pinned against
    `sigma = -E alpha dT`. A uniform field must reproduce `thermal.thermal_load`
    bit for bit, or the generalisation has changed the answer it generalises.
    """

    DELTA_T = 120.0
    REFERENCE = 293.15

    def uniform_matches(self, mesh: TetMesh) -> None:
        """The field path and the uniform path must agree to round-off.

        The tolerance is absolute and scaled to the largest entry rather than
        relative per entry: many entries of a self-equilibrated load vector are
        exact cancellations, and a relative tolerance on a number that is only
        1e-12 because two 1e4 terms cancelled is a comparison of round-off with
        round-off. Measured worst absolute disagreement on tet10: 4.5e-12 N
        against a largest entry of order 1e4 N.
        """
        uniform = np.full(mesh.node_count, self.REFERENCE + self.DELTA_T)
        expected = thermal_load(mesh, STEEL, self.DELTA_T)
        tolerance = 1e-9 * float(np.abs(expected).max())
        assert thermal_load_from_field(
            mesh, STEEL, uniform, self.REFERENCE
        ) == pytest.approx(expected, abs=tolerance)

    def test_a_uniform_field_reproduces_the_uniform_load_on_tet4(self) -> None:
        self.uniform_matches(bar())

    def test_a_uniform_field_reproduces_the_uniform_load_on_tet10(self) -> None:
        self.uniform_matches(bar(quadratic=True))

    def test_a_field_at_the_reference_temperature_produces_no_load(self) -> None:
        mesh = bar()
        at_rest = np.full(mesh.node_count, self.REFERENCE)
        assert thermal_load_from_field(mesh, STEEL, at_rest, self.REFERENCE) == pytest.approx(
            0.0, abs=1e-18
        )

    def test_a_non_uniform_field_is_self_equilibrated(self) -> None:
        """A free part carries no net force however it is heated.

        The thermal load is a set of internal equivalent forces; if they summed
        to anything, an unrestrained heated part would accelerate.
        """
        mesh = bar()
        field = self.linear_field(mesh)
        forces = thermal_load_from_field(mesh, STEEL, field, self.REFERENCE).reshape(-1, 3)
        scale = np.abs(forces).sum()
        assert scale > 0.0
        assert forces.sum(axis=0) == pytest.approx(0.0, abs=1e-9 * scale)

    def linear_field(self, mesh: TetMesh) -> np.ndarray:
        return self.REFERENCE + self.DELTA_T * mesh.nodes[:, 2] / BAR_LENGTH_MM

    def test_the_load_does_the_work_the_temperature_integral_says_it_should(self) -> None:
        """The closed form that pins **where the field is sampled**, both orders.

        Contract the thermal load with the displacement `u(x) = x`, a unit
        dilatation. Then

            f . u = integral epsilon(u)^T D epsilon_thermal dV
                  = (sum of the 3x3 normal block of D) * alpha * integral dT dV

        and for a field linear in z the last integral is its mid-height value
        times the volume. Every term on the right is known before the solver
        runs, and the left-hand side collapses the whole assembly into one
        number that *does* depend on which point of each element the field was
        read at -- unlike the load's resultant, which is zero however badly it
        was sampled, and unlike a uniform field, where every sampling rule
        agrees.
        """
        for quadratic in (False, True):
            mesh = bar(divisions=(2, 2, 4), quadratic=quadratic)
            forces = thermal_load_from_field(
                mesh, STEEL, self.linear_field(mesh), self.REFERENCE
            )
            # u_i = the node's own coordinate: exactly representable by both
            # element orders, because promote_to_tet10 puts midsides at midpoints.
            dilatation = mesh.nodes.ravel()
            alpha = STEEL.thermal_expansion_per_k
            assert alpha is not None
            integral_dt_dv = 0.5 * self.DELTA_T * mesh.volume
            expected = (
                float(constitutive_matrix(STEEL)[:3, :3].sum()) * alpha * integral_dt_dv
            )
            assert float(forces @ dilatation) == pytest.approx(expected, rel=1e-9)

    def test_a_quadratic_field_is_read_at_the_gauss_points_not_at_the_centroid(self) -> None:
        """tet10 only, and it is what pins the Gauss loop rather than the rule.

        A field **linear** in z cannot see the difference: the mean of a linear
        function over an element is its centroid value, so reading the centroid
        once integrates it exactly and the test above passes either way. A
        quadratic field is where the two part company -- and a tet10 interpolates
        a quadratic exactly, and the four-point rule is exact to degree two,
        which is exactly what the contracted integrand is. So the closed form is
        still available: `integral (z/L)^2 dV = V/3`.

        There is no tet4 counterpart, on purpose. A linear element cannot
        represent this field, so the only "expected" value available for it is
        the one the implementation would compute -- which is not a check.
        """
        mesh = bar(divisions=(2, 2, 4), quadratic=True)
        rise = self.DELTA_T * (mesh.nodes[:, 2] / BAR_LENGTH_MM) ** 2
        forces = thermal_load_from_field(mesh, STEEL, self.REFERENCE + rise, self.REFERENCE)
        alpha = STEEL.thermal_expansion_per_k
        assert alpha is not None
        expected = (
            float(constitutive_matrix(STEEL)[:3, :3].sum())
            * alpha
            * self.DELTA_T
            * mesh.volume
            / 3.0
        )
        assert float(forces @ mesh.nodes.ravel()) == pytest.approx(expected, rel=1e-9)

    def test_the_element_change_is_the_centroid_value(self) -> None:
        for quadratic in (False, True):
            mesh = bar(quadratic=quadratic)
            field = self.linear_field(mesh)
            centroids = mesh.nodes[mesh.tets].mean(axis=1)
            expected = self.DELTA_T * centroids[:, 2] / BAR_LENGTH_MM
            assert element_temperature_change(
                mesh, field, self.REFERENCE
            ) == pytest.approx(expected, abs=1e-9)

    def test_the_stress_correction_matches_the_uniform_one_element_by_element(self) -> None:
        mesh = bar()
        uniform = np.full(mesh.tet_count, self.DELTA_T)
        expected = thermal_stress_correction(STEEL, self.DELTA_T)
        rows = thermal_stress_correction_field(STEEL, uniform)
        assert rows.shape == (mesh.tet_count, 6)
        for row in rows:
            assert row == pytest.approx(expected, rel=1e-12)

    def test_the_correction_shears_nothing(self) -> None:
        rows = thermal_stress_correction_field(STEEL, np.array([50.0, -30.0]))
        assert rows[:, 3:] == pytest.approx(0.0, abs=1e-18)
        assert rows[0, 0] > 0.0 and rows[1, 0] < 0.0

    def test_a_material_with_no_expansion_coefficient_is_refused_by_name(self) -> None:
        mesh = bar()
        unmeasured = Material(
            name="unmeasured",
            youngs_modulus_mpa=200_000.0,
            poissons_ratio=0.3,
            yield_strength_mpa=250.0,
            density_kg_m3=7850.0,
        )
        with pytest.raises(SolverError, match="thermal_expansion_per_k"):
            thermal_load_from_field(
                mesh, unmeasured, np.full(mesh.node_count, 400.0), self.REFERENCE
            )


class TestTheUnitBoundary:
    def test_the_conversions_are_the_powers_of_ten_they_claim_to_be(self) -> None:
        """One millimetre is a thousandth of a metre, so a per-metre quantity
        loses a factor of 1000 per length in its denominator."""
        assert CONDUCTIVITY_W_MK_TO_W_MMK == pytest.approx(1e-3)

    def test_the_biot_number_closed_form_is_dimensionless_in_its_length(self) -> None:
        """Doubling the bar and halving the film leaves the tip where it was."""
        first = convecting_bar_tip_temperature_k(500.0, 300.0, 50.0, 200.0, 100.0)
        second = convecting_bar_tip_temperature_k(500.0, 300.0, 50.0, 100.0, 200.0)
        assert first == pytest.approx(second)

    def test_the_logarithmic_profile_hits_both_wall_temperatures(self) -> None:
        assert hollow_cylinder_temperature_k(20.0, 20.0, 60.0, 500.0, 300.0) == pytest.approx(
            500.0
        )
        assert hollow_cylinder_temperature_k(60.0, 20.0, 60.0, 500.0, 300.0) == pytest.approx(
            300.0
        )


class TestTheSeamItSitsBehind:
    """Built on 2026-09-08 and reachable from nothing until 2026-09-09.

    The module's own docstring named the gap: no ABC beside `Solver` and
    `ModalSolver`, so nothing could ask for a conduction solver without naming
    the concrete class, and a second implementation — CalculiX's `*HEAT
    TRANSFER` step — would have had nowhere to sit. These pin the seam itself
    rather than any physics, because a seam nobody can substitute across is
    decoration.
    """

    def test_the_solver_implements_the_conduction_abc(self) -> None:
        assert issubclass(SteadyConductionSolver, ConductionSolver)
        assert isinstance(SteadyConductionSolver(), ConductionSolver)

    def test_the_abc_cannot_be_instantiated_on_its_own(self) -> None:
        """An ABC with no abstract method is a base class with extra words."""
        with pytest.raises(TypeError):
            ConductionSolver()  # type: ignore[abstract]

    def test_a_conduction_solver_is_not_a_stress_solver(self) -> None:
        """The whole reason for a fourth sibling.

        If this ever passed the other way round, `Solver.solve` would be taking
        a union of cases and returning a union of outputs, and every caller
        would be branching on what it got back.
        """
        assert not issubclass(SteadyConductionSolver, Solver)
        assert not issubclass(SteadyConductionSolver, ModalSolver)

    def test_the_recorded_name_is_the_implementation_not_the_analysis(self) -> None:
        """Decision 3 binds a result to what produced it. `steady-conduction` is
        an implementation; a row saying `conduction` would name the question."""
        assert SteadyConductionSolver().name == "steady-conduction"


class TestTheRunIsMetered:
    """A heavy step nobody timed is a heavy step nobody can explain.

    `solve.conduction` is declared in `app/observe/catalogue.py` and its call
    site is here; the pair of tests in `tests/test_observe_report.py` refuses
    either half without the other.
    """

    @staticmethod
    def case() -> ThermalCase:
        return ThermalCase(conductivity_w_mk=50.0, boundaries=held_ends(500.0, 300.0))

    def solved(self, mesh: TetMesh):
        with collect() as recorder:
            SteadyConductionSolver().solve(mesh, self.case())
        return recorder

    def test_a_solve_emits_the_span(self) -> None:
        recorder = self.solved(bar())

        assert [s.name for s in recorder.spans] == ["solve.conduction"]

    def test_the_span_carries_what_makes_a_duration_explicable(self) -> None:
        """Four seconds, against four seconds for three thousand nodes."""
        mesh = bar()
        (span_record,) = self.solved(mesh).spans

        assert span_record.fields["nodes"] == mesh.node_count
        assert span_record.fields["elements"] == mesh.tet_count

    def test_the_degrees_of_freedom_are_one_per_node_not_three(self) -> None:
        """The field exists precisely to make a conduction run comparable with a
        structural one: the same node count is a third of the matrix, so a
        report holding only node counts would call the difference variance."""
        mesh = bar()
        (span_record,) = self.solved(mesh).spans

        assert span_record.fields["degrees_of_freedom"] == mesh.node_count
        assert span_record.fields["degrees_of_freedom"] != 3 * mesh.node_count

    def test_the_span_says_which_stage_it_ended_in(self) -> None:
        """Assembly and factorisation have different fixes, so one duration over
        both cannot answer the question it was taken to answer."""
        (span_record,) = self.solved(bar()).spans

        assert span_record.fields["stage"] == "factorise"
        assert span_record.fields["method"] == "direct"

    def test_a_refused_solve_is_still_a_measurement(self) -> None:
        """The run that blew up is exactly the one somebody wants the timing
        for. A failed span is recorded and marked failed, never dropped."""
        case = ThermalCase(
            conductivity_w_mk=50.0,
            boundaries=[
                Convection(
                    where=BoxSelector(min=(4.0, 4.0, 40.0), max=(6.0, 6.0, 60.0)),
                    film_coefficient_w_m2k=25.0,
                    ambient_temperature_k=300.0,
                    name="film on nothing",
                )
            ],
        )
        with collect() as recorder:
            with pytest.raises(SolverError):
                SteadyConductionSolver().solve(bar(), case)

        (span_record,) = recorder.spans
        assert span_record.name == "solve.conduction"
        assert not span_record.ok

    def test_collecting_nothing_costs_the_solve_nothing(self) -> None:
        """`observe.span` returns the shared inert object when nobody is
        collecting, so the physics tests above run through the same code path
        without paying for it."""
        field = SteadyConductionSolver().solve(bar(), self.case())

        assert field.result.max_temperature_k == pytest.approx(500.0)


class TestTheRegistryCanSelectIt:
    """The other half of "reachable": a name in a setting, not a constructor
    call. Covered in depth in `tests/test_solver_registry.py`; asserted here so
    that renaming the concrete class without updating the factory fails in the
    module that owns the class."""

    def test_the_internal_name_builds_this_solver(self) -> None:
        built = build_conduction_solver(INTERNAL)

        # By class *name*, not `isinstance`. `tests/test_solver_registry.py`
        # proves the factory's import is lazy by deleting the module from
        # `sys.modules`, and the next import rebuilds the class object — so an
        # identity check here passes or fails on which file pytest ran first.
        assert type(built).__name__ == SteadyConductionSolver.__name__
        assert built.name == SteadyConductionSolver.name
