"""The load types added beyond a plain force vector.

Each is checked against a closed-form answer rather than against itself. That
matters more here than usual: a load that assembles without error and applies
the wrong magnitude produces a plausible stress plot, and nothing downstream
would ever flag it.

The shared fixture is a 10x10x40 mm bar, clamped at z=0, meshed coarsely —
these test the *load assembly*, not the element formulation, which
`test_solver.py` already covers against beam theory.
"""

import numpy as np
import pytest

from app.mesh.primitives import box_mesh
from app.solve.linear_static import LinearStaticSolver
from app.solve.loads import assemble_loads
from app.solve.types import (
    STANDARD_GRAVITY_MM_S2,
    BearingLoad,
    BoxSelector,
    CentrifugalLoad,
    CylinderSelector,
    FaceSelector,
    Fixture,
    ForceLoad,
    GravityLoad,
    LoadCase,
    Material,
    MomentLoad,
    PressureLoad,
    SolverError,
)

WIDTH = DEPTH = 10.0
HEIGHT = 40.0

STEEL = Material(
    name="steel-test",
    youngs_modulus_mpa=200_000.0,
    poissons_ratio=0.3,
    yield_strength_mpa=250.0,
    density_kg_m3=7850.0,
)


@pytest.fixture(scope="module")
def bar():
    return box_mesh((WIDTH, DEPTH, HEIGHT), divisions=(3, 3, 8))


def _clamp():
    return Fixture(where=FaceSelector(axis="z", side="min"), name="base")


def _resultant(mesh, loads, density=STEEL.density_kg_m3):
    """Net force vector the assembled load applies, in N."""
    forces, warnings = assemble_loads(mesh, loads, density)
    return forces.reshape(-1, 3).sum(axis=0), warnings


class TestPressure:
    def test_resultant_is_pressure_times_area(self, bar):
        """A pressure on the top face must total p x A, pushing inward."""
        pressure = 2.0  # MPa
        resultant, _ = _resultant(
            bar,
            [PressureLoad(where=FaceSelector(axis="z", side="max"), pressure_mpa=pressure)],
        )
        expected = pressure * WIDTH * DEPTH
        # Positive pressure pushes into the material: the top face is loaded -z.
        assert resultant[2] == pytest.approx(-expected, rel=1e-6)
        assert resultant[0] == pytest.approx(0.0, abs=expected * 1e-9)
        assert resultant[1] == pytest.approx(0.0, abs=expected * 1e-9)

    def test_it_scales_with_area_where_a_force_would_not(self, bar):
        """The distinction that makes pressure a separate type at all."""
        big = box_mesh((2 * WIDTH, DEPTH, HEIGHT), divisions=(6, 3, 8))
        load = [PressureLoad(where=FaceSelector(axis="z", side="max"), pressure_mpa=1.0)]
        small_total = abs(_resultant(bar, load)[0][2])
        big_total = abs(_resultant(big, load)[0][2])
        assert big_total == pytest.approx(2.0 * small_total, rel=1e-6)

    def test_negative_pressure_pulls(self, bar):
        resultant, _ = _resultant(
            bar,
            [PressureLoad(where=FaceSelector(axis="z", side="max"), pressure_mpa=-1.5)],
        )
        assert resultant[2] > 0.0

    def test_a_pressure_on_interior_nodes_is_refused(self, bar):
        """A pressure needs a surface; a box of interior nodes is not one."""
        # Strictly inside the bar, so it catches interior nodes and no
        # complete boundary facet.
        interior = BoxSelector(min=(3.0, 3.0, 14.0), max=(4.0, 4.0, 16.0))
        with pytest.raises(SolverError, match="no complete surface facets"):
            assemble_loads(
                bar, [PressureLoad(where=interior, pressure_mpa=1.0)], STEEL.density_kg_m3
            )

    def test_axial_stress_matches_the_equivalent_force(self, bar):
        """Pressure and the equivalent force must give the same answer."""
        area = WIDTH * DEPTH
        by_pressure = LinearStaticSolver().solve(
            bar,
            LoadCase(
                material=STEEL,
                fixtures=[_clamp()],
                loads=[PressureLoad(where=FaceSelector(axis="z", side="max"), pressure_mpa=-1.0)],
            ),
        )
        by_force = LinearStaticSolver().solve(
            bar,
            LoadCase(
                material=STEEL,
                fixtures=[_clamp()],
                loads=[
                    ForceLoad(
                        where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, area)
                    )
                ],
            ),
        )
        assert by_pressure.result.max_von_mises_mpa == pytest.approx(
            by_force.result.max_von_mises_mpa, rel=1e-6
        )


class TestGravity:
    def test_resultant_is_mass_times_g(self, bar):
        """Self-weight must total m x g, and nothing else."""
        resultant, _ = _resultant(bar, [GravityLoad()])
        volume_mm3 = WIDTH * DEPTH * HEIGHT
        mass_tonne = STEEL.density_kg_m3 * 1e-12 * volume_mm3
        expected = mass_tonne * STANDARD_GRAVITY_MM_S2
        assert resultant[2] == pytest.approx(-expected, rel=1e-6)
        assert abs(resultant[0]) < expected * 1e-9
        assert abs(resultant[1]) < expected * 1e-9

    def test_it_is_quadratic_in_nothing_but_linear_in_g(self, bar):
        single = abs(_resultant(bar, [GravityLoad()])[0][2])
        double = abs(
            _resultant(
                bar, [GravityLoad(magnitude_mm_s2=2.0 * STANDARD_GRAVITY_MM_S2)]
            )[0][2]
        )
        assert double == pytest.approx(2.0 * single, rel=1e-9)

    def test_denser_material_weighs_more(self, bar):
        light = abs(_resultant(bar, [GravityLoad()], density=1000.0)[0][2])
        heavy = abs(_resultant(bar, [GravityLoad()], density=8000.0)[0][2])
        assert heavy == pytest.approx(8.0 * light, rel=1e-9)

    def test_a_zero_direction_is_refused(self, bar):
        with pytest.raises(SolverError, match="no direction"):
            assemble_loads(
                bar, [GravityLoad(direction=(0.0, 0.0, 0.0))], STEEL.density_kg_m3
            )


class TestCentrifugal:
    def test_load_is_quadratic_in_speed(self, bar):
        """Doubling rpm quadruples the load. The thing people get wrong."""
        axis = {"axis_point": (0.0, 0.0, 0.0), "axis_direction": (0.0, 0.0, 1.0)}
        slow, _ = _resultant(bar, [CentrifugalLoad(rpm=1000.0, **axis)])
        fast, _ = _resultant(bar, [CentrifugalLoad(rpm=2000.0, **axis)])
        assert np.linalg.norm(fast) == pytest.approx(
            4.0 * np.linalg.norm(slow), rel=1e-9
        )

    def test_force_points_away_from_the_axis(self, bar):
        """A bar offset in +x from the axis must be flung in +x."""
        resultant, _ = _resultant(
            bar,
            [
                CentrifugalLoad(
                    axis_point=(-50.0, 0.0, 0.0),
                    axis_direction=(0.0, 0.0, 1.0),
                    rpm=3000.0,
                )
            ],
        )
        assert resultant[0] > 0.0

    def test_a_body_on_its_own_axis_is_balanced(self, bar):
        """Spinning about its own centreline gives zero net force."""
        resultant, _ = _resultant(
            bar,
            [
                CentrifugalLoad(
                    axis_point=(WIDTH / 2, DEPTH / 2, 0.0),
                    axis_direction=(0.0, 0.0, 1.0),
                    rpm=5000.0,
                )
            ],
        )
        # Balanced, but not zero *load* -- the material is still in tension.
        assert np.linalg.norm(resultant) < 1e-6


class TestMoment:
    def test_it_applies_the_requested_moment_and_no_net_force(self, bar):
        """sum(r x F) must equal the moment, and sum(F) must vanish."""
        moment = (0.0, 0.0, 5000.0)
        where = FaceSelector(axis="z", side="max")
        forces, _ = assemble_loads(
            bar, [MomentLoad(where=where, moment_n_mm=moment)], STEEL.density_kg_m3
        )
        vectors = forces.reshape(-1, 3)

        assert np.linalg.norm(vectors.sum(axis=0)) < 1e-6

        loaded = np.flatnonzero(np.linalg.norm(vectors, axis=1) > 0.0)
        centroid = bar.nodes[loaded].mean(axis=0)
        applied = np.cross(bar.nodes - centroid, vectors).sum(axis=0)
        assert applied == pytest.approx(np.asarray(moment), rel=1e-6, abs=1e-6)

    def test_a_zero_moment_is_refused(self, bar):
        with pytest.raises(SolverError, match="applies nothing"):
            assemble_loads(
                bar,
                [
                    MomentLoad(
                        where=FaceSelector(axis="z", side="max"),
                        moment_n_mm=(0.0, 0.0, 0.0),
                    )
                ],
                STEEL.density_kg_m3,
            )

    def test_a_region_on_the_axis_has_no_lever_arm(self, bar):
        """Every node on the axis: no force distribution can make the moment."""
        on_axis = BoxSelector(min=(4.9, 4.9, 39.0), max=(5.1, 5.1, 40.0))
        with pytest.raises(SolverError, match="lever arm|matched no nodes"):
            assemble_loads(
                bar,
                [MomentLoad(where=on_axis, moment_n_mm=(0.0, 0.0, 1.0))],
                STEEL.density_kg_m3,
            )


class TestBearing:
    @staticmethod
    @pytest.fixture(scope="module")
    def bore():
        """A block standing in for a lug.

        It has no actual hole: `box_mesh` cannot make one, and what these tests
        check is the *distribution* -- which half of a cylindrical region carries
        the load and how sharply -- which an annulus of nodes exercises exactly
        as a real bore would. The element formulation is covered elsewhere.
        """
        return box_mesh((40.0, 40.0, 10.0), divisions=(8, 8, 2))

    def test_only_the_loaded_half_carries_the_load(self, bore):
        """A pin pushes on the half of the bore facing the load, not all of it."""
        where = CylinderSelector(
            axis_point=(20.0, 20.0, 0.0),
            axis_direction=(0.0, 0.0, 1.0),
            radius=20.0,
            radius_tolerance=3.0,
        )
        force = (1000.0, 0.0, 0.0)
        forces, _ = assemble_loads(
            bore, [BearingLoad(where=where, force_n=force)], STEEL.density_kg_m3
        )
        vectors = forces.reshape(-1, 3)

        # The resultant is what was asked for.
        assert vectors.sum(axis=0) == pytest.approx(np.asarray(force), rel=1e-6)

        # And it is carried by the +x half only. A pin pushing the lug in +x
        # presses on the bore wall at greater x, so that is the material that
        # takes the load -- reasoning from the surface normal instead (which
        # points into the hole) gives the opposite half and is the classic slip.
        loaded = np.flatnonzero(np.linalg.norm(vectors, axis=1) > 1e-12)
        assert len(loaded) > 0
        assert bore.nodes[loaded][:, 0].mean() > 20.0

    def test_a_higher_exponent_concentrates_the_load(self, bore):
        where = CylinderSelector(
            axis_point=(20.0, 20.0, 0.0),
            axis_direction=(0.0, 0.0, 1.0),
            radius=20.0,
            radius_tolerance=3.0,
        )

        def peak(exponent):
            forces, _ = assemble_loads(
                bore,
                [
                    BearingLoad(
                        where=where, force_n=(1000.0, 0.0, 0.0), distribution=exponent
                    )
                ],
                STEEL.density_kg_m3,
            )
            return np.linalg.norm(forces.reshape(-1, 3), axis=1).max()

        assert peak(3.0) > peak(1.0)

    def test_it_refuses_a_non_cylindrical_region(self, bore):
        """Without a bore axis there is no 'half facing the load' to find."""
        with pytest.raises(SolverError, match="cylinder selector"):
            assemble_loads(
                bore,
                [
                    BearingLoad(
                        where=FaceSelector(axis="z", side="max"),
                        force_n=(100.0, 0.0, 0.0),
                    )
                ],
                STEEL.density_kg_m3,
            )


class TestRestraintKinds:
    def test_a_roller_holds_only_its_normal(self):
        fixture = Fixture(
            where=FaceSelector(axis="z", side="min"), kind="roller", normal="z"
        )
        assert fixture.dofs == ["z"]

    def test_a_slider_holds_the_other_two(self):
        fixture = Fixture(
            where=FaceSelector(axis="z", side="min"), kind="slider", normal="z"
        )
        assert fixture.dofs == ["x", "y"]

    def test_a_clamp_holds_everything(self):
        fixture = Fixture(where=FaceSelector(axis="z", side="min"), kind="clamp")
        assert fixture.dofs == ["x", "y", "z"]

    def test_symmetry_is_a_roller_by_another_name(self):
        roller = Fixture(
            where=FaceSelector(axis="x", side="min"), kind="roller", normal="x"
        )
        symmetry = Fixture(
            where=FaceSelector(axis="x", side="min"), kind="symmetry", normal="x"
        )
        assert roller.dofs == symmetry.dofs

    def test_a_dofs_that_contradicts_the_kind_is_refused(self):
        """A roller normal to z holds z; saying it holds x is two answers."""
        with pytest.raises(ValueError, match="disagree"):
            Fixture(
                where=FaceSelector(axis="z", side="min"),
                kind="roller",
                normal="z",
                dofs=["x"],
            )

    def test_a_dofs_that_agrees_with_the_kind_is_accepted(self):
        """Redundant but consistent. Refusing it would break revalidation.

        Pydantic re-validates a nested model instance in some configurations,
        and by then the validator has already filled `dofs` from `kind` -- so a
        rule phrased as "never both" rejects the object it just built.
        """
        fixture = Fixture(
            where=FaceSelector(axis="z", side="min"),
            kind="roller",
            normal="z",
            dofs=["z"],
        )
        assert fixture.dofs == ["z"]

    def test_a_validated_fixture_survives_being_validated_again(self):
        original = Fixture(where=FaceSelector(axis="z", side="min"), kind="clamp")
        again = Fixture.model_validate(original.model_dump())
        assert again.dofs == original.dofs == ["x", "y", "z"]

    def test_a_roller_without_a_normal_is_refused(self):
        with pytest.raises(ValueError, match="needs `normal`"):
            Fixture(where=FaceSelector(axis="z", side="min"), kind="roller")

    def test_a_roller_really_does_let_the_face_slide(self, bar):
        """The behavioural difference, not just the dof list."""
        clamped = LinearStaticSolver().solve(
            bar,
            LoadCase(
                material=STEEL,
                fixtures=[Fixture(where=FaceSelector(axis="z", side="min"), kind="clamp")],
                loads=[
                    ForceLoad(
                        where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, 5000.0)
                    )
                ],
            ),
        )
        # A pure axial pull on a roller-supported base still needs the in-plane
        # motion removed somewhere, so the comparison uses two extra rollers
        # rather than leaving the model free to drift.
        rollered = LinearStaticSolver().solve(
            bar,
            LoadCase(
                material=STEEL,
                fixtures=[
                    Fixture(where=FaceSelector(axis="z", side="min"), kind="roller", normal="z"),
                    Fixture(where=FaceSelector(axis="x", side="min"), kind="roller", normal="x"),
                    Fixture(where=FaceSelector(axis="y", side="min"), kind="roller", normal="y"),
                ],
                loads=[
                    ForceLoad(
                        where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, 5000.0)
                    )
                ],
            ),
        )
        # A clamped base resists the Poisson contraction and so carries a local
        # stress concentration a roller base does not.
        assert clamped.result.max_von_mises_mpa > rollered.result.max_von_mises_mpa


class TestBackwardsCompatibility:
    def test_a_stored_load_case_without_a_type_still_solves(self, bar):
        """Saved simulations must keep re-solving to the same answer."""
        stored = {
            "name": "from before the union",
            "material": STEEL.model_dump(),
            "fixtures": [{"where": {"type": "face", "axis": "z", "side": "min"}}],
            "loads": [
                {
                    "where": {"type": "face", "axis": "z", "side": "max"},
                    "force_n": [0.0, 0.0, 1000.0],
                }
            ],
        }
        case = LoadCase.model_validate(stored)
        assert isinstance(case.loads[0], ForceLoad)
        assert case.fixtures[0].dofs == ["x", "y", "z"]

        result = LinearStaticSolver().solve(bar, case).result
        explicit = (
            LinearStaticSolver()
            .solve(
                bar,
                LoadCase(
                    material=STEEL,
                    fixtures=[_clamp()],
                    loads=[
                        ForceLoad(
                            where=FaceSelector(axis="z", side="max"),
                            force_n=(0.0, 0.0, 1000.0),
                        )
                    ],
                ),
            )
            .result
        )
        assert result.max_von_mises_mpa == pytest.approx(explicit.max_von_mises_mpa)
