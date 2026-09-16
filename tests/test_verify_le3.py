"""NAFEMS LE3, the hemisphere under point loads — the first benchmark solved on a shell.

Two halves, as `app/verify/nafems.py` has them. The offline half proves the
model is the one the sources pose and that the pieces a run strings together
agree: one sewn shell of the right area, a node on every named point, four
loads that balance, six restraints that remove six rigid-body modes and no
more, a read-out that is half the diametral change, and a provenance record and
a convergence level that accept a `ShellMesh`. The ccx half solves one grid and
holds it to the published 185 mm; it skips on a machine with no ccx, and a skip
is not a pass.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction
that Windows runs the tests. THE QUEUE A6 carries the run.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.kernel.occt.binding import available
from app.mesh.structural import ShellMesh
from app.mesh.types import MeshError
from app.solve.calculix.run import find_ccx
from app.solve.calculix.shell import ShellSolver, write_shell_deck
from app.solve.shell_loads import assemble_shell_loads
from app.verify import le3_geometry, nafems
from app.verify.benchmarks import TargetBasis
from app.verify.convergence import _level_from
from app.verify.le3_geometry import (
    LE3_AREA_MM2,
    LE3_POINT_A,
    LE3_POINT_A_OPPOSITE,
    LE3_POINT_C,
    LE3_POINT_C_OPPOSITE,
    LE3_POINT_E,
    LE3_RADIUS_MM,
    LE3_THICKNESS_MM,
    hemisphere_shell,
)
from app.verify.nafems import (
    BY_ID,
    LE3_ELEMENT_SIZES_MM,
    LE3_MATERIAL,
    LE3_POINT_LOAD_N,
    LE3_QUANTITY,
    le3_case,
    le3_section,
)
from app.verify.provenance import RunProvenance, identify_solver, mesh_digest

needs_kernel = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)
needs_ccx = pytest.mark.skipif(find_ccx() is None, reason="no ccx on this machine")

#: Coarse enough to mesh in a second or two and still put a node on every named
#: point; not one of the study's sizes, because these tests are about the model,
#: not its discretisation error.
_COARSE_MM = 1_500.0


@pytest.fixture(scope="module")
def coarse_mesh() -> ShellMesh:
    if not available():
        pytest.skip("OCCT (cadquery-ocp) is not installed in this environment")
    from app.manufacture.export import write_step
    from app.mesh.gmsh_mesher import generate_shell_mesh

    with tempfile.TemporaryDirectory(prefix="le3-test-") as workspace:
        step = Path(workspace) / "le3.step"
        write_step(hemisphere_shell(), step)
        mesh, _ = generate_shell_mesh(
            step, "step", element_size_mm=_COARSE_MM, element_order=2, face_shape="tri"
        )
    return mesh


def _nearest(mesh: ShellMesh, point: tuple[float, float, float]) -> tuple[int, float]:
    distance = np.linalg.norm(mesh.nodes - np.asarray(point), axis=1)
    node = int(np.argmin(distance))
    return node, float(distance[node])


class TestTheShapeIsTheSourcedOne:
    def test_the_radius_and_thickness_are_the_sourced_metres_in_mm(self) -> None:
        assert (LE3_RADIUS_MM, LE3_THICKNESS_MM) == (10_000.0, 40.0)

    def test_the_named_points_follow_the_figures_triad(self) -> None:
        """z polar, x through A, y through C (the NAFEMS figure's triad)."""
        assert LE3_POINT_A == (LE3_RADIUS_MM, 0.0, 0.0)
        assert LE3_POINT_C == (0.0, LE3_RADIUS_MM, 0.0)
        assert LE3_POINT_E == (0.0, 0.0, LE3_RADIUS_MM)
        assert LE3_POINT_A_OPPOSITE == (-LE3_RADIUS_MM, 0.0, 0.0)
        assert LE3_POINT_C_OPPOSITE == (0.0, -LE3_RADIUS_MM, 0.0)

    def test_the_exact_area_is_a_hemisphere_not_a_quarter(self) -> None:
        assert LE3_AREA_MM2 == pytest.approx(2.0 * math.pi * 1e8)

    def test_a_radius_that_is_not_positive_is_refused(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            hemisphere_shell(0.0)

    @needs_kernel
    def test_the_hemisphere_is_one_sewn_shell_of_four_faces_and_the_exact_area(
        self,
    ) -> None:
        """Four disconnected faces were the gmsh-written model's defect: the load
        at A landed on two unjoined nodes. Sewing is what makes it one shell."""
        from app.kernel.occt.binding import symbol
        from app.kernel.occt.topology import FACE, SHELL, _enum, explore

        shape = hemisphere_shell()
        assert shape.ShapeType() == _enum(SHELL)
        assert len(explore(shape, FACE)) == 4

        props = symbol("GProp_GProps")()
        symbol("BRepGProp").SurfaceProperties_s(shape, props)
        assert props.Mass() == pytest.approx(LE3_AREA_MM2, rel=1e-6)


class TestTheMeshCarriesEveryNamedPoint:
    @pytest.mark.parametrize(
        "point",
        [LE3_POINT_A, LE3_POINT_A_OPPOSITE, LE3_POINT_C, LE3_POINT_C_OPPOSITE, LE3_POINT_E],
    )
    def test_a_node_sits_on_the_point(self, coarse_mesh: ShellMesh, point: Any) -> None:
        _, distance = _nearest(coarse_mesh, point)
        assert distance < nafems.LE3_POINT_TOLERANCE_MM

    def test_exactly_one_node_answers_each_point_selector(self, coarse_mesh: ShellMesh) -> None:
        """Two coincident nodes at A would split the load between an unjoined
        pair — the defect the sewn geometry exists to prevent."""
        for point in (LE3_POINT_A, LE3_POINT_A_OPPOSITE, LE3_POINT_C, LE3_POINT_C_OPPOSITE):
            close = np.linalg.norm(coarse_mesh.nodes - np.asarray(point), axis=1)
            assert int((close <= nafems.LE3_POINT_TOLERANCE_MM).sum()) == 1

    def test_no_two_nodes_coincide_anywhere(self, coarse_mesh: ShellMesh) -> None:
        rounded = np.round(coarse_mesh.nodes, 3)
        assert len(np.unique(rounded, axis=0)) == coarse_mesh.node_count

    def test_the_mesh_is_tri6_and_inside_the_area_tolerance(self, coarse_mesh: ShellMesh) -> None:
        assert coarse_mesh.element_type == "tri6"
        shortfall = abs(coarse_mesh.area_mm2 - LE3_AREA_MM2) / LE3_AREA_MM2
        assert shortfall < nafems.LE3_AREA_TOLERANCE


class TestTheLoadCaseIsTheQuarterModelMadeWhole:
    def test_each_point_carries_twice_the_quarter_models_two_kilonewtons(self) -> None:
        assert LE3_POINT_LOAD_N == 2.0 * 2_000.0

    def test_the_material_is_the_one_the_benchmark_states(self) -> None:
        assert LE3_MATERIAL.youngs_modulus_mpa == pytest.approx(68_250.0)
        assert LE3_MATERIAL.poissons_ratio == pytest.approx(0.3)

    def test_the_section_is_forty_millimetres_on_the_mid_surface(self) -> None:
        section = le3_section()
        assert section.thickness_mm == pytest.approx(40.0)
        assert section.offset == pytest.approx(0.0)

    def test_the_four_loads_balance_in_force_and_moment(self) -> None:
        """Self-equilibrated loads are what let three isostatic supports carry
        nothing; a sign slip on one arrow breaks this before it reaches ccx."""
        total = np.zeros(3)
        moment = np.zeros(3)
        for load in le3_case().loads:
            force = np.asarray(load.force_n)
            where = np.asarray(load.where.centre)
            total += force
            moment += np.cross(where, force)
        assert np.allclose(total, 0.0)
        assert np.allclose(moment, 0.0)

    def test_a_and_a_prime_pull_outward_and_c_and_c_prime_push_inward(self) -> None:
        for load in le3_case().loads:
            radial = np.dot(np.asarray(load.force_n), np.asarray(load.where.centre))
            outward = load.where.centre[0] != 0.0
            # `bool(...)`, because `np.float64 > float` is a `np.bool_` and
            # `np.bool_(True) is True` is False -- an identity check against a
            # numpy scalar can never pass. The assertion was written this way on
            # Linux and executed nowhere until 2026-09-17.
            assert bool(radial > 0.0) is outward

    def test_six_translational_restraints_and_no_rotational_one(self) -> None:
        fixtures = le3_case().fixtures
        dofs = sorted(
            (fixture.where.centre, dof) for fixture in fixtures for dof in fixture.dofs or []
        )
        assert len(dofs) == 6
        assert {dof for _, dof in dofs} <= {"x", "y", "z"}
        assert dofs == sorted(
            [
                (LE3_POINT_E, "x"),
                (LE3_POINT_E, "y"),
                (LE3_POINT_E, "z"),
                (LE3_POINT_A, "y"),
                (LE3_POINT_A, "z"),
                (LE3_POINT_C, "z"),
            ]
        )

    def test_the_supports_remove_all_six_rigid_body_modes(self) -> None:
        """Rank of the rigid-body motions sampled at the restrained dofs.

        A rigid motion is u(p) = t + w x p. Each restraint reads one component
        of it; six independent readings leave no motion unrestrained."""
        rows = []
        for fixture in le3_case().fixtures:
            p = np.asarray(fixture.where.centre)
            for dof in fixture.dofs or []:
                axis = "xyz".index(dof)
                row = np.zeros(6)
                row[axis] = 1.0
                for w in range(3):
                    omega = np.zeros(3)
                    omega[w] = 1.0
                    row[3 + w] = np.cross(omega, p)[axis]
                rows.append(row)
        assert np.linalg.matrix_rank(np.asarray(rows)) == 6

    def test_every_load_lands_whole_on_one_node(self, coarse_mesh: ShellMesh) -> None:
        forces, _ = assemble_shell_loads(
            coarse_mesh, list(le3_case().loads), LE3_THICKNESS_MM, LE3_MATERIAL.density_kg_m3
        )
        per_node = forces.reshape(-1, 3)
        for load in le3_case().loads:
            node, _ = _nearest(coarse_mesh, load.where.centre)
            assert per_node[node] == pytest.approx(np.asarray(load.force_n))
        assert np.count_nonzero(np.linalg.norm(per_node, axis=1)) == 4

    def test_the_deck_writes_without_ccx(self, coarse_mesh: ShellMesh) -> None:
        deck, _ = write_shell_deck(coarse_mesh, le3_case(), le3_section())
        assert "*SHELL SECTION" in deck.upper()
        assert "OUTPUT=2D" in deck.upper()


class TestTheReadOutIsHalfTheDiametralChange:
    def _mesh(self) -> SimpleNamespace:
        return SimpleNamespace(
            nodes=np.array([LE3_POINT_A, LE3_POINT_A_OPPOSITE, LE3_POINT_E], dtype=float)
        )

    def test_it_is_half_of_ux_at_a_minus_ux_at_a_prime(self) -> None:
        output = SimpleNamespace(
            displacements=np.array([[190.0, 0.0, 0.0], [-180.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
        )
        assert LE3_QUANTITY.read(self._mesh(), output) == pytest.approx(185.0)

    def test_a_rigid_translation_in_x_does_not_move_it(self) -> None:
        base = np.array([[185.0, 0.0, 0.0], [-185.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
        shifted = base + np.array([37.0, 0.0, 0.0])
        mesh = self._mesh()
        assert LE3_QUANTITY.read(mesh, SimpleNamespace(displacements=base)) == pytest.approx(
            LE3_QUANTITY.read(mesh, SimpleNamespace(displacements=shifted))
        )

    def test_a_mesh_with_no_node_on_a_is_refused_by_name(self) -> None:
        mesh = SimpleNamespace(
            nodes=np.array([[LE3_RADIUS_MM - 50.0, 0.0, 0.0], LE3_POINT_A_OPPOSITE], dtype=float)
        )
        output = SimpleNamespace(displacements=np.zeros((2, 3)))
        with pytest.raises(MeshError, match="point A"):
            LE3_QUANTITY.read(mesh, output)

    def test_the_unit_is_millimetres(self) -> None:
        assert LE3_QUANTITY.unit == "mm"


class TestTheCatalogueRunsIt:
    def test_le3_runs_and_names_no_blocker(self) -> None:
        case = BY_ID["nafems-le3"]
        assert case.benchmark.runnable
        assert case.blocker is None
        assert case.benchmark.run is nafems.run_le3

    def test_it_is_slow_so_the_default_path_does_not_solve_three_grids(self) -> None:
        assert BY_ID["nafems-le3"].benchmark.slow

    def test_the_target_is_still_the_published_185_mm_at_2_percent(self) -> None:
        target = BY_ID["nafems-le3"].benchmark.target
        assert target.basis is TargetBasis.PUBLISHED
        assert (target.value, target.unit, target.tolerance) == (185.0, "mm", 0.02)

    def test_the_sizes_are_three_levels_on_the_one_point_four_rule(self) -> None:
        sizes = LE3_ELEMENT_SIZES_MM
        assert len(sizes) == 3
        assert list(sizes) == sorted(sizes, reverse=True)
        for coarse, fine in zip(sizes, sizes[1:], strict=False):
            # A band, not a point. The rule is "about 1.4x in representative
            # size" -- wide enough that the discretisation trend dominates
            # gmsh's remeshing noise, which 1.2 does not. 500/355 is 1.4085 and
            # 355/250 is 1.42, and `approx(1.4, abs=0.02)` failed the second by
            # one ulp: the difference computes to 0.020000000000000018. A
            # tolerance whose edge a chosen size lands exactly on is a tolerance
            # that tests the float format rather than the spacing.
            assert 1.35 <= coarse / fine <= 1.45

    def test_no_case_is_blocked_on_a_shell_solver_any_more(self) -> None:
        assert "no-shell-solver" not in {str(b) for b in nafems.Blocker}
        assert nafems.blockers() == {}

    def test_every_le3_source_is_cited_by_the_case(self) -> None:
        references = set(BY_ID["nafems-le3"].benchmark.references)
        for key in ("abaqus-le3", "nafems-le3-figure", "esrd-le3-geometry", "altair-le3"):
            assert nafems.SOURCES[key] in references

    def test_the_geometry_module_names_no_number_it_did_not_source(self) -> None:
        """Every public constant is a named point, the radius, the thickness or
        the area derived from the radius — nothing else is allowed in."""
        public = {name for name in le3_geometry.__all__ if name.isupper()}
        assert public == {
            "LE3_AREA_MM2",
            "LE3_POINT_A",
            "LE3_POINT_A_OPPOSITE",
            "LE3_POINT_C",
            "LE3_POINT_C_OPPOSITE",
            "LE3_POINT_E",
            "LE3_RADIUS_MM",
            "LE3_THICKNESS_MM",
        }


class TestAShellMeshIsAFirstClassRecord:
    def test_the_convergence_study_places_a_shell_as_a_two_dimensional_level(
        self, coarse_mesh: ShellMesh
    ) -> None:
        level = _level_from(coarse_mesh, element_size_mm=_COARSE_MM, value=180.0)
        assert level.dimension == 2
        assert level.area_mm2 == pytest.approx(coarse_mesh.area_mm2)
        assert level.element_count == coarse_mesh.face_count

    def test_the_provenance_digest_covers_the_shell_faces(self, coarse_mesh: ShellMesh) -> None:
        moved = ShellMesh(
            nodes=coarse_mesh.nodes.copy(),
            faces=coarse_mesh.faces[:, [1, 2, 0]].copy(),
            midside=None if coarse_mesh.midside is None else coarse_mesh.midside[:, [1, 2, 0]],
        )
        assert mesh_digest(coarse_mesh) != mesh_digest(moved)

    def test_a_provenance_record_on_a_shell_measures_area_not_volume(
        self, coarse_mesh: ShellMesh
    ) -> None:
        record = RunProvenance(
            analysis="linear-static",
            quantity=LE3_QUANTITY.name,
            unit="mm",
            value=184.0,
            geometry_source="test hemisphere",
            mesh=coarse_mesh,
            case=le3_case(),
            solver=identify_solver(ShellSolver()),
            element_size_mm=_COARSE_MM,
        )
        payload = record.to_dict()
        assert "area_mm2" in payload["mesh"]
        assert "volume_mm3" not in payload["mesh"]
        assert payload["mesh"]["element_count"] == coarse_mesh.face_count
        assert payload["mesh"]["sliver_count"] is None


@needs_kernel
@needs_ccx
class TestCalculiXReproducesTheReference:
    def test_the_finest_study_grid_is_inside_two_percent_of_185_mm(self) -> None:
        from app.manufacture.export import write_step
        from app.mesh.gmsh_mesher import generate_shell_mesh

        with tempfile.TemporaryDirectory(prefix="le3-ccx-") as workspace:
            step = Path(workspace) / "le3.step"
            write_step(hemisphere_shell(), step)
            mesh, _ = generate_shell_mesh(
                step,
                "step",
                element_size_mm=min(LE3_ELEMENT_SIZES_MM),
                element_order=2,
                face_shape="tri",
            )
        output = ShellSolver(timeout_s=nafems.LE3_TIMEOUT_S).solve(mesh, le3_case(), le3_section())
        value = LE3_QUANTITY.read(mesh, output)

        assert value == pytest.approx(185.0, rel=0.02)
