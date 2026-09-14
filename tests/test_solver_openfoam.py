"""Laminar internal flow through OpenFOAM — `app/solve/openfoam/`, master plan E10 task 2.

Two halves, and they prove different things.

**Offline** (always runs): the surface split by selector, the dictionaries, the
refusals, and the readers — on text a test wrote. None of it needs OpenFOAM.

**Against a real OpenFOAM** (skips when the pinned image is not available, and
says so): closed forms, each through the whole path — surface, snappyHexMesh,
checkMesh, simpleFoam, the function objects.

* **Hagen–Poiseuille**: a round pipe, fully developed, has −dp/dz = 8νU/R² and a
  centreline velocity of 2U. The pipe is curved, so this is the test of the
  snapping — "the meshing is the hard part".
* **The rectangular duct's series solution** (Poisson's equation on a rectangle,
  summed): for sides 2a × 2b with a ≥ b, −dp/dz = 3νU / (b² [1 − (192 b / π⁵ a)
  Σ_{odd i} tanh(iπa/2b)/i⁵]). Its geometry comes from the job's own kind of tet
  mesh, and its inlet and outlet from `FaceSelector`s, so this is the path a job takes.
* **The Graetz limits** for heat carried by that laminar pipe flow: fully developed,
  Nu = 3.657 with the wall at one temperature and Nu = 48/11 = 4.364 with one heat
  flux through it — Shah & London's values, the textbook pair. Pe = 100, where
  axial conduction moves the first by about 1e-4 (Nu ≈ 3.657(1 + 1.227/Pe²)), so the
  closed form applies. With a flux the heat in is known exactly, so the balance
  between it and the heat carried out is a second, independent check.

Measured on `opencfd/openfoam-default:2412`, 16 cells across the pipe (49,920
cells, 125 SIMPLE iterations, ~31 s): developed pressure gradient +0.42%,
centreline velocity −0.96%; square duct from a tet mesh (96,000 cells) −0.001%;
Nu on an isothermal wall +0.30% (fitted over z = 80–140 mm); Nu under a flux
+1.4% to +2.4% depending on the section — a wall face's temperature under a
fixed gradient is first order on a snapped cell — and an energy balance of 0.085%.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from app.mesh.primitives import box_mesh
from app.mesh.types import TetMesh
from app.solve.openfoam import results
from app.solve.openfoam.case import (
    LAMINAR_REYNOLDS_LIMIT,
    FlowCase,
    FlowInlet,
    FlowOutlet,
    Fluid,
    ForcedConvection,
    WallHeatFlux,
    WallTemperature,
)
from app.solve.openfoam.dictionaries import (
    COMMANDS,
    ENERGY_RESIDUAL_TARGET,
    background_cells,
    case_files,
    run_script,
)
from app.solve.openfoam.run import OpenFoamUnavailable, availability, engine_identity, run_case
from app.solve.openfoam.solver import ENERGY_BALANCE_WARNING, LaminarFlowSolver, check_case
from app.solve.openfoam.surface import INLET, OUTLET, REGIONS, WALL, DuctSurface, duct_surface
from app.solve.types import BodySelector, BoxSelector, FaceSelector, SolverError

WATER = Fluid(name="water", kinematic_viscosity_mm2_s=1.0, density_kg_m3=1000.0)

#: Conductivity that makes α = k/(ρ c) exactly 1 mm²/s with WATER's density and this
#: specific heat: Pr = 1, so Pe = Re = 100 in the 10 mm pipe at 10 mm/s.
UNIT_DIFFUSIVITY_K = 4.18
SPECIFIC_HEAT = 4180.0


def _heat(wall: WallTemperature | WallHeatFlux, inlet_k: float = 300.0) -> ForcedConvection:
    return ForcedConvection(
        inlet_temperature_k=inlet_k,
        wall=wall,
        conductivity_w_mk=UNIT_DIFFUSIVITY_K,
        specific_heat_j_kgk=SPECIFIC_HEAT,
    )


def _case(**overrides: object) -> FlowCase:
    values: dict[str, object] = {
        "fluid": WATER,
        "inlet": FlowInlet(where=FaceSelector(axis="z", side="min"), mean_velocity_mm_s=10.0),
        "outlet": FlowOutlet(where=FaceSelector(axis="z", side="max")),
        "cell_size_mm": 0.5,
    }
    values.update(overrides)
    return FlowCase(**values)  # type: ignore[arg-type]


def _duct(length: float = 60.0, divisions: tuple[int, int, int] = (2, 2, 6)) -> TetMesh:
    return box_mesh((10.0, 10.0, length), divisions)


def _pipe(radius: float = 5.0, length: float = 150.0, segments: int = 96, rings: int = 60) -> DuctSurface:
    """A closed, outward-wound cylinder: wall, and a cap at each end."""
    theta = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    nodes = [(0.0, 0.0, 0.0), (0.0, 0.0, length)]
    for z in np.linspace(0.0, length, rings + 1):
        nodes += [(radius * math.cos(t), radius * math.sin(t), float(z)) for t in theta]
    ring = lambda k, i: 2 + k * segments + (i % segments)  # noqa: E731
    triangles, regions = [], []
    for k in range(rings):
        for i in range(segments):
            triangles += [(ring(k, i), ring(k, i + 1), ring(k + 1, i + 1)), (ring(k, i), ring(k + 1, i + 1), ring(k + 1, i))]
            regions += [REGIONS.index(WALL)] * 2
    for i in range(segments):
        triangles.append((0, ring(0, i + 1), ring(0, i)))
        regions.append(REGIONS.index(INLET))
        triangles.append((1, ring(rings, i), ring(rings, i + 1)))
        regions.append(REGIONS.index(OUTLET))
    return DuctSurface(
        nodes=np.asarray(nodes, dtype=np.float64),
        triangles=np.asarray(triangles, dtype=np.int64),
        regions=np.asarray(regions, dtype=np.int64),
        inside=(0.0137, 0.0219, length / 2.0 + 0.0311),
    )


class TestTheSurfaceIsSplitBySelector:
    def test_inlet_outlet_and_wall_areas(self) -> None:
        surface = duct_surface(_duct(), _case())
        assert surface.area_of(INLET) == pytest.approx(100.0)
        assert surface.area_of(OUTLET) == pytest.approx(100.0)
        assert surface.area_of(WALL) == pytest.approx(4 * 10.0 * 60.0)

    def test_it_is_closed_and_wound_outward(self) -> None:
        surface = duct_surface(_duct(), _case())
        assert np.allclose(surface.normals().sum(axis=0), 0.0, atol=1e-9)
        assert np.allclose(surface.inward_direction(INLET), (0.0, 0.0, 1.0))
        assert np.allclose(surface.inward_direction(OUTLET), (0.0, 0.0, -1.0))

    def test_an_inverted_element_does_not_turn_a_face_inside_out(self) -> None:
        mesh = _duct()
        flipped = TetMesh(nodes=mesh.nodes, tets=mesh.tets[:, [0, 2, 1, 3]])
        assert np.all(flipped.signed_volumes() < 0.0)
        assert np.allclose(duct_surface(flipped, _case()).inward_direction(INLET), (0.0, 0.0, 1.0))

    def test_the_hydraulic_diameter_is_four_area_over_perimeter(self) -> None:
        mesh = box_mesh((20.0, 10.0, 60.0), (4, 2, 6))
        assert duct_surface(mesh, _case()).hydraulic_diameter() == pytest.approx(4 * 200.0 / 60.0)

    def test_the_inside_point_is_inside(self) -> None:
        x, y, z = duct_surface(_duct(), _case()).inside
        assert 0.0 < x < 10.0 and 0.0 < y < 10.0 and 0.0 < z < 60.0

    def test_an_inlet_that_is_also_the_outlet_is_refused(self) -> None:
        everywhere = BoxSelector(min=(-1.0, -1.0, -1.0), max=(11.0, 11.0, 61.0))
        case = _case(
            inlet=FlowInlet(where=everywhere, mean_velocity_mm_s=10.0), outlet=FlowOutlet(where=BodySelector())
        )
        with pytest.raises(SolverError, match="both claim"):
            duct_surface(_duct(), case)

    def test_a_selection_with_no_whole_face_is_refused(self) -> None:
        edge = BoxSelector(min=(-0.1, -0.1, -0.1), max=(0.1, 10.1, 0.1))
        with pytest.raises(SolverError, match="no whole boundary face"):
            duct_surface(_duct(), _case(inlet=FlowInlet(where=edge, mean_velocity_mm_s=10.0)))

    def test_the_stl_names_one_solid_per_patch(self) -> None:
        surface = duct_surface(_duct(), _case())
        text = surface.stl()
        for name in (INLET, OUTLET, WALL):
            body = text.split(f"solid {name}\n", 1)[1].split(f"endsolid {name}\n", 1)[0]
            assert body.count("facet normal") == int(np.sum(surface.regions == REGIONS.index(name)))


class TestTheCaseFiles:
    def test_the_inlet_velocity_points_into_the_duct(self) -> None:
        files = case_files(_case(), duct_surface(_duct(), _case()))
        assert "inlet { type fixedValue; value uniform (0 0 10); }" in files["0/U"]
        assert "outlet { type fixedValue; value uniform 0; }" in files["0/p"]
        assert "wall { type noSlip; }" in files["0/U"]

    def test_the_fluid_and_the_iteration_limit_reach_the_dictionaries(self) -> None:
        files = case_files(_case(max_iterations=321), duct_surface(_duct(), _case()))
        assert "nu 1;" in files["constant/transportProperties"]
        assert "simulationType laminar;" in files["constant/turbulenceProperties"]
        assert "endTime 321;" in files["system/controlDict"]
        assert "residualControl { p 1e-05; U 1e-06; }" in files["system/fvSolution"]

    def test_the_background_grid_covers_the_surface_with_a_margin(self) -> None:
        surface = duct_surface(_duct(), _case())
        low, high, counts = background_cells(surface, 0.5)
        assert low == pytest.approx((-1.0, -1.0, -1.0))
        assert high == pytest.approx((11.0, 11.0, 61.0))
        assert counts == (24, 24, 124)

    def test_the_location_in_mesh_is_the_surfaces_inside_point(self) -> None:
        surface = duct_surface(_duct(), _case())
        x, y, z = surface.inside
        assert f"locationInMesh ({x:.12g} {y:.12g} {z:.12g});" in case_files(_case(), surface)["system/snappyHexMeshDict"]

    def test_each_step_fails_with_its_own_exit_code(self) -> None:
        script = run_script()
        for index, (name, _) in enumerate(COMMANDS):
            assert f"> log.{name} 2>&1 || exit {10 + index}" in script
        assert script.index('cd "$(dirname "$0")"') < script.index("blockMesh")


class TestTheHeatReachesTheCase:
    def test_no_heat_writes_no_temperature(self) -> None:
        files = case_files(_case(), duct_surface(_duct(), _case()))
        assert "0/T" not in files
        assert "scalarTransport" not in files["system/controlDict"]
        assert "div(phi,T)" not in files["system/fvSchemes"]
        assert "    T {" not in files["system/fvSolution"]

    def test_the_units_convert_once(self) -> None:
        water = ForcedConvection(
            inlet_temperature_k=293.15, wall=WallHeatFlux(flux_w_m2=5000.0), conductivity_w_mk=0.6,
            specific_heat_j_kgk=4180.0,
        )
        # 0.6 / (1000 × 4180) m²/s = 1.435e-7 m²/s = 0.1435 mm²/s; 5000/0.6 = 8333 K/m = 8.333 K/mm.
        assert water.diffusivity_mm2_s(1000.0) == pytest.approx(0.143540669856)
        assert water.wall_gradient_k_mm() == pytest.approx(8.333333333)
        assert _heat(WallTemperature(temperature_k=310.0)).wall_gradient_k_mm() is None

    def test_an_isothermal_wall(self) -> None:
        case = _case(heat=_heat(WallTemperature(temperature_k=310.0)))
        files = case_files(case, duct_surface(_duct(), case))
        assert "inlet { type fixedValue; value uniform 300; }" in files["0/T"]
        assert "wall { type fixedValue; value uniform 310; }" in files["0/T"]
        assert "outlet { type zeroGradient; }" in files["0/T"]
        assert "D 1;" in files["system/controlDict"]

    def test_a_heat_flux_is_a_gradient_rising_towards_the_wall(self) -> None:
        case = _case(heat=_heat(WallHeatFlux(flux_w_m2=1000.0)))
        files = case_files(case, duct_surface(_duct(), case))
        assert f"wall {{ type fixedGradient; gradient uniform {1000.0 / 4.18 * 1e-3:.12g}; }}" in files["0/T"]
        cooled = _case(heat=_heat(WallHeatFlux(flux_w_m2=-1000.0)))
        assert "gradient uniform -0.239" in case_files(cooled, duct_surface(_duct(), cooled))["0/T"]

    def test_the_temperature_is_solved_before_anything_reads_it(self) -> None:
        case = _case(heat=_heat(WallTemperature(temperature_k=310.0)))
        control = case_files(case, duct_surface(_duct(), case))["system/controlDict"]
        solved = control.index("type scalarTransport;")
        for reader in ("outletTemperature", "wallMeanTemperature", "wallMaxTemperature", "wallTemperatureSurface"):
            assert solved < control.index(reader)

    def test_the_outlet_temperature_is_the_bulk_one(self) -> None:
        case = _case(heat=_heat(WallTemperature(temperature_k=310.0)))
        control = case_files(case, duct_surface(_duct(), case))["system/controlDict"]
        outlet = control[control.index("outletTemperature"):control.index("wallMeanTemperature")]
        assert "operation weightedAverage;" in outlet and "weightField phi;" in outlet

    def test_the_temperature_has_a_solver_that_stops_on_an_absolute_tolerance(self) -> None:
        case = _case(heat=_heat(WallTemperature(temperature_k=310.0)))
        files = case_files(case, duct_surface(_duct(), case))
        assert "    T { solver PBiCGStab; preconditioner DILU; tolerance 1e-11; relTol 0; }" in files["system/fvSolution"]
        assert "div(phi,T) bounded Gauss linearUpwind grad(T);" in files["system/fvSchemes"]
        # Not in SIMPLE's convergence test: a passive temperature must not hold the flow open.
        assert "residualControl { p 1e-05; U 1e-06; }" in files["system/fvSolution"]


class TestACaseThatWouldMisleadIsRefused:
    def test_a_turbulent_inlet(self) -> None:
        surface = duct_surface(_duct(), _case())
        fast = _case(inlet=FlowInlet(where=FaceSelector(axis="z", side="min"), mean_velocity_mm_s=500.0))
        with pytest.raises(SolverError, match=f"above the laminar limit of {LAMINAR_REYNOLDS_LIMIT:.0f}.*Below 200 mm/s"):
            check_case(surface, fast)

    def test_too_few_cells_across(self) -> None:
        surface = duct_surface(_duct(), _case())
        with pytest.raises(SolverError, match="4.0 cells across.*1.25 or less"):
            check_case(surface, _case(cell_size_mm=2.5))

    def test_a_background_grid_too_large_to_start(self) -> None:
        surface = duct_surface(box_mesh((10.0, 10.0, 6000.0), (2, 2, 10)), _case())
        with pytest.raises(SolverError, match="over the limit"):
            check_case(surface, _case(cell_size_mm=0.1))

    def test_an_acceptable_case_reports_its_diameter_and_reynolds_number(self) -> None:
        assert check_case(duct_surface(_duct(), _case()), _case()) == pytest.approx((10.0, 100.0))


class TestTheReaders:
    def test_a_nonuniform_vector_and_scalar_field(self) -> None:
        vector = "dimensions [0 1 -1 0 0 0 0];\ninternalField   nonuniform List<vector> \n2\n(\n(1 2 3)\n(4 5 6)\n)\n;\n"
        scalar = "internalField   nonuniform List<scalar> \n3\n(\n1.5\n-2\n3e-3\n)\n;\n"
        assert results.read_internal_field(vector).tolist() == [[1, 2, 3], [4, 5, 6]]
        assert results.read_internal_field(scalar).tolist() == [1.5, -2.0, 0.003]

    def test_a_uniform_field_needs_the_cell_count(self) -> None:
        text = "internalField   uniform (0 0 2);\n"
        assert results.read_internal_field(text, cells=2).tolist() == [[0, 0, 2], [0, 0, 2]]
        with pytest.raises(SolverError, match="no internal field"):
            results.read_internal_field(text)

    def test_a_field_shorter_than_it_says_is_refused(self) -> None:
        with pytest.raises(SolverError, match="announced 3 values and held 2"):
            results.read_internal_field("internalField nonuniform List<scalar> 3 ( 1 2 ) ;")

    def test_the_last_row_of_a_function_object_table(self) -> None:
        table = "# Time areaAverage(p)\n1\t5.0\n2\t4.5\n"
        assert results.last_value(table) == 4.5
        with pytest.raises(SolverError, match="no rows"):
            results.last_value("# header only\n")

    def test_a_solver_log_that_converged(self) -> None:
        log = (
            "|  \\\\    /   O peration     | Version:  2412                                  |\n"
            "Build  : _45e7c4a0-20241224 OPENFOAM=2412 version=2412\n"
            "Time = 124\n\nsmoothSolver:  Solving for Uz, Initial residual = 3e-07, Final\n"
            "Time = 125\n\nsmoothSolver:  Solving for Uz, Initial residual = 2e-07, Final\n"
            "GAMG:  Solving for p, Initial residual = 9e-06, Final\n\n"
            "SIMPLE solution converged in 125 iterations\n\nEnd\n"
        )
        read = results.read_solver_log(log)
        assert read.converged and read.iterations == 125
        assert read.version == "2412 (build _45e7c4a0-20241224)"
        assert read.residuals == {"Uz": 2e-07, "p": 9e-06}

    def test_a_solver_log_that_ran_out_of_iterations_is_not_converged(self) -> None:
        log = "Time = 2000\n\nGAMG:  Solving for p, Initial residual = 0.004, Final\n\nEnd\n"
        read = results.read_solver_log(log)
        assert not read.converged and read.iterations == 2000 and read.residuals == {"p": 0.004}

    def test_kinematic_pressure_becomes_mpa_once(self) -> None:
        # 1 m²/s² of water is 1000 Pa: 1e6 mm²/s² × 1000 kg/m³ × 1e-12 = 1e-3 MPa.
        assert results.kinematic_to_mpa(1e6, 1000.0) == pytest.approx(1e-3)

    def test_the_temperature_residual_is_read(self) -> None:
        log = (
            "Time = 125\n\nGAMG:  Solving for p, Initial residual = 9e-06, Final\n\n"
            "SIMPLE solution converged in 125 iterations\n\nscalarTransport execute: T\n"
            "DILUPBiCGStab:  Solving for T, Initial residual = 3.4e-10, Final residual = 6e-13\n\nEnd\n"
        )
        assert results.read_solver_log(log).residuals == {"p": 9e-06, "T": 3.4e-10}

    def test_a_raw_surface_file(self) -> None:
        text = "# T  FACE_DATA 2\n# x y z  T\n1 2 3 300.5\n4 5 6 301\n"
        centres, values = results.read_raw_surface(text)
        assert centres.tolist() == [[1, 2, 3], [4, 5, 6]] and values.tolist() == [300.5, 301.0]
        with pytest.raises(SolverError, match="announced 3 faces and held 2"):
            results.read_raw_surface(text.replace("FACE_DATA 2", "FACE_DATA 3"))

    def test_the_patch_area_is_the_meshes_own(self) -> None:
        table = "# Region type :     patch wall\n# Faces : 10560\n# Area              : 4.706270936499e+03\n1\t2\n"
        assert results.patch_area(table) == pytest.approx(4706.270936499)
        with pytest.raises(SolverError, match="no patch area"):
            results.patch_area("# Faces : 1\n1\t2\n")

    def test_check_mesh(self) -> None:
        assert results.mesh_ok("...\nMesh OK.\n\nEnd\n")
        assert not results.mesh_ok("***Error in mesh\nFailed 1 mesh checks.\n")
        assert results.read_cell_count("    points:           100\n    cells:            46127\n") == 46127


class TestNotInstalledIsNotFailed:
    def test_no_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.solve.openfoam.run.shutil.which", lambda name: None)
        assert "no `docker` executable" in (availability("docker") or "")
        with pytest.raises(OpenFoamUnavailable, match="docker"):
            run_case(Path("."), launcher="docker")

    def test_no_local_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.solve.openfoam.run.shutil.which", lambda name: None)
        assert "not on PATH" in (availability("local") or "")

    def test_an_unknown_launcher(self) -> None:
        assert "must be one of docker, local" in (availability("kubernetes") or "")

    def test_the_image_is_never_pulled_during_a_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.solve.openfoam.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.solve.openfoam.run._image_present", lambda image: False)
        assert "docker pull" in (availability("docker", "example/openfoam:1") or "")


class TestTheEngineIsNamedByItsBytes:
    """What the job cache keys a flow run on. A tag can be re-pushed; the id cannot."""

    @staticmethod
    def _inspect(stdout: str, returncode: int = 0) -> object:
        import subprocess

        return lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    def test_a_docker_image_is_named_by_tag_and_content_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.solve.openfoam.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.solve.openfoam.run.subprocess.run", self._inspect("sha256:abc123\n"))
        assert engine_identity("docker", "example/openfoam:1") == "docker example/openfoam:1 sha256:abc123"

    def test_an_image_whose_id_cannot_be_read_is_not_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.solve.openfoam.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.solve.openfoam.run.subprocess.run", self._inspect("", returncode=1))
        assert engine_identity("docker", "example/openfoam:1") is None
        # Something that is not a content id is not taken for one.
        monkeypatch.setattr("app.solve.openfoam.run.subprocess.run", self._inspect("example/openfoam:1\n"))
        assert engine_identity("docker", "example/openfoam:1") is None

    def test_a_local_install_is_not_named_before_it_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.solve.openfoam.run.shutil.which", lambda name: "/usr/bin/simpleFoam")
        assert engine_identity("local") is None


class _FakeRun:
    """Stands in for OpenFOAM: writes the logs a run would, and exits as told."""

    def __init__(self, returncode: int, logs: dict[str, str]) -> None:
        self.returncode, self.logs = returncode, logs

    def __call__(self, directory: Path, **kwargs: object) -> object:
        from app.solve.openfoam.run import FoamRun

        for name, text in self.logs.items():
            (directory / f"log.{name}").write_text(text, encoding="utf-8")
        return FoamRun(returncode=self.returncode, output="", seconds=0.0, launcher="docker")


class TestAFailedRunSaysWhichStep:
    def test_a_snapping_failure_names_snappy_hex_mesh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.solve.openfoam.solver.run_case", _FakeRun(11, {"snappyHexMesh": "--> FOAM FATAL ERROR: no cells\n"})
        )
        with pytest.raises(SolverError, match="(?s)snappyHexMesh failed.*closed.*no cells"):
            LaminarFlowSolver().solve(_duct(), _case())

    def test_an_unconverged_run_returns_no_pressure_drop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = {
            "checkMesh": "    cells:            1000\nMesh OK.\n",
            "simpleFoam": "Time = 50\n\nGAMG:  Solving for p, Initial residual = 0.02, Final\n\nEnd\n",
        }
        monkeypatch.setattr("app.solve.openfoam.solver.run_case", _FakeRun(0, logs))
        with pytest.raises(SolverError, match="did not converge in 50 iterations.*p 0.02"):
            LaminarFlowSolver().solve(_duct(), _case(max_iterations=50))

    def test_a_mesh_that_fails_its_checks_is_not_solved_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = {"checkMesh": "    cells:            1000\nFailed 2 mesh checks.\n", "simpleFoam": ""}
        monkeypatch.setattr("app.solve.openfoam.solver.run_case", _FakeRun(0, logs))
        with pytest.raises(SolverError, match="checkMesh did not pass"):
            LaminarFlowSolver().solve(_duct(), _case())


class _FakeConvergedRun:
    """A converged heated run on a two-cell mesh, with the tables a test chooses."""

    def __init__(self, tables: dict[str, float], temperature_residual: float | None = 3e-10) -> None:
        self.tables, self.temperature_residual = tables, temperature_residual

    def __call__(self, directory: Path, **kwargs: object) -> object:
        from app.solve.openfoam.run import FoamRun

        solve = (
            f"DILUPBiCGStab:  Solving for T, Initial residual = {self.temperature_residual}, Final\n"
            if self.temperature_residual is not None
            else ""
        )
        (directory / "log.checkMesh").write_text("    cells:            2\nMesh OK.\n")
        (directory / "log.simpleFoam").write_text(
            "Time = 7\n\nGAMG:  Solving for p, Initial residual = 1e-06, Final\n\n"
            f"SIMPLE solution converged in 7 iterations\n\n{solve}\nEnd\n"
        )
        latest = directory / "7"
        latest.mkdir()
        for name, value in {"U": "(0 0 10)", "p": "0", "C": "(0 0 0)", "V": "1", "T": "305"}.items():
            (latest / name).write_text(f"internalField uniform {value};\n")
        for name, value in self.tables.items():
            table = directory / "postProcessing" / name / "0"
            table.mkdir(parents=True)
            (table / "surfaceFieldValue.dat").write_text(f"# Area : 1000\n# Time value\n7\t{value}\n")
        surface = directory / "postProcessing" / "wallTemperatureSurface" / "surface" / "7"
        surface.mkdir(parents=True)
        (surface / "T_patch_wall.raw").write_text("# T  FACE_DATA 1\n# x y z  T\n0 0 1 309\n")
        return FoamRun(returncode=0, output="", seconds=0.0, launcher="docker")


#: Tables for a duct carrying 1000 mm³/s: 1e-6 m³/s of water heats 4.18 W per kelvin.
_FLOWING = {"inletFlux": -1000.0, "outletFlux": 1000.0, "inletPressure": 0.0, "outletPressure": 0.0}


class TestWhatAHeatedRunReports:
    def _solve(self, monkeypatch: pytest.MonkeyPatch, wall: WallTemperature | WallHeatFlux, **tables: float) -> object:
        values = _FLOWING | {"wallMeanTemperature": 305.0, "wallMaxTemperature": 309.0} | tables
        monkeypatch.setattr("app.solve.openfoam.solver.run_case", _FakeConvergedRun(values))
        return LaminarFlowSolver().solve(_duct(), _case(heat=_heat(wall)))

    def test_the_heat_carried_and_the_log_mean_coefficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        output = self._solve(monkeypatch, WallTemperature(temperature_k=310.0), outletTemperature=305.0)
        heat = output.result.heat  # type: ignore[attr-defined]
        # 1000 kg/m³ × 4180 J/(kg K) × 1e-6 m³/s × 5 K = 20.9 W.
        assert heat.heat_to_fluid_w == pytest.approx(20.9)
        log_mean = (10.0 - 5.0) / math.log(10.0 / 5.0)
        assert heat.mean_heat_transfer_coefficient_w_m2k == pytest.approx(20.9 / (1000.0 * 1e-6 * log_mean))
        assert heat.wall_area_mm2 == 1000.0 and heat.wall_heat_input_w is None and heat.energy_balance_error is None
        assert heat.prandtl_number == pytest.approx(1.0) and heat.peclet_number == pytest.approx(100.0)
        assert heat.wall_max_temperature_k == 309.0 and heat.outlet_bulk_temperature_k == 305.0
        assert output.wall_temperatures_k.tolist() == [309.0]  # type: ignore[attr-defined]
        assert output.temperature_k.tolist() == [305.0, 305.0]  # type: ignore[attr-defined]
        assert output.result.warnings == []  # type: ignore[attr-defined]

    def test_a_temperature_that_did_not_settle_is_not_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        values = _FLOWING | {"outletTemperature": 305.0, "wallMeanTemperature": 305.0, "wallMaxTemperature": 309.0}
        monkeypatch.setattr(
            "app.solve.openfoam.solver.run_case", _FakeConvergedRun(values, temperature_residual=10 * ENERGY_RESIDUAL_TARGET)
        )
        with pytest.raises(SolverError, match="temperature did not settle"):
            LaminarFlowSolver().solve(_duct(), _case(heat=_heat(WallTemperature(temperature_k=310.0))))
        monkeypatch.setattr("app.solve.openfoam.solver.run_case", _FakeConvergedRun(values, temperature_residual=None))
        with pytest.raises(SolverError, match="not recorded"):
            LaminarFlowSolver().solve(_duct(), _case(heat=_heat(WallTemperature(temperature_k=310.0))))

    def test_a_flux_wall_balances_its_heat_and_has_no_coefficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 20.9 W over 1000 mm² is 20,900 W/m²; the fluid rising 5 K carries exactly that.
        output = self._solve(monkeypatch, WallHeatFlux(flux_w_m2=20_900.0), outletTemperature=305.0)
        heat = output.result.heat  # type: ignore[attr-defined]
        assert heat.wall_heat_input_w == pytest.approx(20.9)
        assert heat.energy_balance_error == pytest.approx(0.0, abs=1e-12)
        assert heat.mean_heat_transfer_coefficient_w_m2k is None
        assert output.result.warnings == []  # type: ignore[attr-defined]

    def test_a_flux_wall_that_does_not_balance_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rise = 5.0 * (1.0 + 2.0 * ENERGY_BALANCE_WARNING)
        output = self._solve(monkeypatch, WallHeatFlux(flux_w_m2=20_900.0), outletTemperature=300.0 + rise)
        assert output.result.heat.energy_balance_error == pytest.approx(2.0 * ENERGY_BALANCE_WARNING)  # type: ignore[attr-defined]
        assert any("apart" in warning for warning in output.result.warnings)  # type: ignore[attr-defined]
        within = 5.0 * (1.0 + 0.5 * ENERGY_BALANCE_WARNING)
        quiet = self._solve(monkeypatch, WallHeatFlux(flux_w_m2=20_900.0), outletTemperature=300.0 + within)
        assert quiet.result.warnings == []  # type: ignore[attr-defined]

    def test_an_outlet_hotter_than_the_wall_warns_and_has_no_coefficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        output = self._solve(monkeypatch, WallTemperature(temperature_k=310.0), outletTemperature=311.0)
        assert output.result.heat.mean_heat_transfer_coefficient_w_m2k is None  # type: ignore[attr-defined]
        assert any("outside the inlet and wall" in warning for warning in output.result.warnings)  # type: ignore[attr-defined]
        colder = self._solve(monkeypatch, WallTemperature(temperature_k=310.0), outletTemperature=299.0)
        assert any("outside the inlet and wall" in warning for warning in colder.result.warnings)  # type: ignore[attr-defined]

    def test_a_wall_at_the_inlet_temperature_moves_no_heat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        output = self._solve(monkeypatch, WallTemperature(temperature_k=300.0), outletTemperature=300.0)
        assert output.result.heat.mean_heat_transfer_coefficient_w_m2k is None  # type: ignore[attr-defined]
        assert any("no heat moves" in warning for warning in output.result.warnings)  # type: ignore[attr-defined]

    def test_a_fluid_that_left_at_the_wall_temperature_has_no_coefficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        output = self._solve(monkeypatch, WallTemperature(temperature_k=310.0), outletTemperature=310.0 - 1e-9)
        assert output.result.heat.mean_heat_transfer_coefficient_w_m2k is None  # type: ignore[attr-defined]
        assert any("longer than the heat needed" in warning for warning in output.result.warnings)  # type: ignore[attr-defined]

    def test_a_cooled_fluid_has_a_positive_coefficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        output = self._solve(monkeypatch, WallTemperature(temperature_k=290.0), outletTemperature=295.0)
        heat = output.result.heat  # type: ignore[attr-defined]
        assert heat.heat_to_fluid_w == pytest.approx(-20.9)
        assert heat.mean_heat_transfer_coefficient_w_m2k > 0.0


def _rectangular_gradient(a: float, b: float, nu: float, velocity: float, terms: int = 50) -> float:
    """−dp/dz (kinematic) for fully developed laminar flow in a 2a × 2b duct, a ≥ b."""
    a, b = max(a, b), min(a, b)
    series = sum(math.tanh(i * math.pi * a / (2.0 * b)) / i**5 for i in range(1, 2 * terms, 2))
    return 3.0 * nu * velocity / (b**2 * (1.0 - 192.0 * b / (math.pi**5 * a) * series))


class TestTheRectangularSeries:
    def test_the_square_duct_is_the_tabulated_poiseuille_number(self) -> None:
        """A sanity check of the helper, not of OpenFOAM: f·Re on the hydraulic
        diameter, from the series, for a square."""
        gradient = _rectangular_gradient(5.0, 5.0, 1.0, 10.0)
        assert 2.0 * 10.0**2 * gradient / (1.0 * 10.0) == pytest.approx(56.9, abs=0.05)

    def test_a_very_wide_duct_tends_to_parallel_plates(self) -> None:
        # Plates 2b apart: −dp/dz = 3νU/b².
        assert _rectangular_gradient(5000.0, 5.0, 1.0, 10.0) == pytest.approx(3.0 * 10.0 / 25.0, rel=1e-3)


_MISSING = availability("docker")
needs_openfoam = pytest.mark.skipif(_MISSING is not None, reason=f"No OpenFOAM to run against: {_MISSING}")


def _bulk_temperature(output: object, z: float, half_width: float = 1.25) -> float:
    """The mixed-mean temperature across a thin slab of cells: Σ u T V / Σ u V."""
    centres, velocity = output.cell_centres, output.velocity  # type: ignore[attr-defined]
    temperature, volumes = output.temperature_k, output.cell_volumes  # type: ignore[attr-defined]
    slab = np.abs(centres[:, 2] - z) < half_width
    flow = velocity[slab, 2] * volumes[slab]
    return float((flow * temperature[slab]).sum() / flow.sum())


def _developed_gradient(output: object, axis_distance: float, z_range: tuple[float, float]) -> float:
    centres = output.cell_centres  # type: ignore[attr-defined]
    radius = np.hypot(centres[:, 0] - 0.0, centres[:, 1] - 0.0) if axis_distance < 0 else np.hypot(
        centres[:, 0] - 5.0, centres[:, 1] - 5.0
    )
    near = (radius < abs(axis_distance)) & (centres[:, 2] > z_range[0]) & (centres[:, 2] < z_range[1])
    return float(-np.polyfit(centres[near, 2], output.kinematic_pressure[near], 1)[0])  # type: ignore[attr-defined]


@needs_openfoam
class TestAPipeIsHagenPoiseuille:
    @pytest.fixture(scope="class")
    @staticmethod
    def pipe() -> object:
        # Heat is passive, so the one run also carries the isothermal-wall Graetz case.
        case = _case(cell_size_mm=0.625, heat=_heat(WallTemperature(temperature_k=310.0)))
        return LaminarFlowSolver().solve_surface(_pipe(), case)

    def test_it_converged_on_a_mesh_that_passed_its_checks(self, pipe: object) -> None:
        result = pipe.result  # type: ignore[attr-defined]
        assert result.iterations > 0 and result.cells > 10_000
        assert result.solver == "openfoam"
        assert result.solver_version is not None and result.solver_version.startswith("2412 (build ")
        assert result.mesh_convergence.basis == "single-grid"

    def test_the_developed_pressure_gradient(self, pipe: object) -> None:
        assert _developed_gradient(pipe, -1.0, (60.0, 140.0)) == pytest.approx(8.0 * 1.0 * 10.0 / 5.0**2, rel=0.01)

    def test_the_centreline_velocity_is_twice_the_mean(self, pipe: object) -> None:
        centres, velocity = pipe.cell_centres, pipe.velocity  # type: ignore[attr-defined]
        axis = (np.hypot(centres[:, 0], centres[:, 1]) < 0.5) & (centres[:, 2] > 115.0) & (centres[:, 2] < 125.0)
        assert float(velocity[axis, 2].mean()) == pytest.approx(20.0, rel=0.01)

    def test_what_goes_in_comes_out(self, pipe: object) -> None:
        result = pipe.result  # type: ignore[attr-defined]
        assert result.continuity_error < 1e-6
        assert result.inlet_flow_rate_mm3_s == pytest.approx(10.0 * math.pi * 25.0, rel=0.01)

    def test_the_pressure_drop_is_the_developed_one_plus_an_entrance(self, pipe: object) -> None:
        # The fully developed gradient over the whole length, in MPa, is a lower bound:
        # the flow enters flat, and accelerating the core in the entrance region costs
        # more than the developed wall shear alone. The upper bound is a sanity band,
        # not a closed form — measured 1.155 times the lower bound on 2412.
        developed = results.kinematic_to_mpa(8.0 * 1.0 * 10.0 / 5.0**2 * 150.0, 1000.0)
        assert developed < pipe.result.pressure_drop_mpa < 1.25 * developed  # type: ignore[attr-defined]

    def test_the_developed_nusselt_number_on_an_isothermal_wall(self, pipe: object) -> None:
        # (T_w − T_b) decays as exp(−4 Nu α z / (U D²)) once developed; α = 1, U = 10, D = 10.
        z = np.arange(80.0, 141.0, 5.0)
        excess = [310.0 - _bulk_temperature(pipe, float(at)) for at in z]
        rate = -np.polyfit(z, np.log(excess), 1)[0]
        assert rate * 10.0 * 10.0**2 / 4.0 == pytest.approx(3.6568, rel=0.01)

    def test_the_heat_carried_is_the_outlet_temperature_rise(self, pipe: object) -> None:
        result = pipe.result  # type: ignore[attr-defined]
        heat = result.heat
        rise = heat.outlet_bulk_temperature_k - 300.0
        assert 0.0 < rise < 10.0
        assert heat.heat_to_fluid_w == pytest.approx(1000.0 * SPECIFIC_HEAT * result.inlet_flow_rate_mm3_s * rise * 1e-9)
        # The solver's flux-weighted outlet and a slab by the outlet tell the same story.
        assert heat.outlet_bulk_temperature_k == pytest.approx(_bulk_temperature(pipe, 148.0), abs=0.05)
        assert heat.wall_max_temperature_k == pytest.approx(310.0) and heat.wall_mean_temperature_k == pytest.approx(310.0)
        assert heat.prandtl_number == pytest.approx(1.0) and heat.peclet_number == pytest.approx(100.0, rel=0.01)

    def test_the_mean_coefficient_includes_the_entrance(self, pipe: object) -> None:
        # Averaged over a length that includes the thermal entrance, where the wall
        # gradient is steepest, the mean Nusselt number is above the developed limit.
        heat = pipe.result.heat  # type: ignore[attr-defined]
        mean_nusselt = heat.mean_heat_transfer_coefficient_w_m2k * 10e-3 / UNIT_DIFFUSIVITY_K
        assert 3.6568 < mean_nusselt < 3.6568 * 2.0


@needs_openfoam
class TestAPipeUnderAUniformHeatFlux:
    @pytest.fixture(scope="class")
    @staticmethod
    def pipe() -> object:
        case = _case(cell_size_mm=0.625, heat=_heat(WallHeatFlux(flux_w_m2=1000.0)))
        return LaminarFlowSolver().solve_surface(_pipe(), case)

    def test_the_heat_put_in_is_the_heat_carried_out(self, pipe: object) -> None:
        heat = pipe.result.heat  # type: ignore[attr-defined]
        assert heat.wall_heat_input_w == pytest.approx(1000.0 * heat.wall_area_mm2 * 1e-6)
        assert heat.wall_area_mm2 == pytest.approx(2.0 * math.pi * 5.0 * 150.0, rel=0.01)
        assert heat.energy_balance_error < 0.005
        assert heat.mean_heat_transfer_coefficient_w_m2k is None

    def test_the_developed_nusselt_number_under_a_flux(self, pipe: object) -> None:
        # T_w − T_b = q D / (k Nu), so Nu = g D / (T_w − T_b) with g = q/k in K/mm.
        gradient = 1000.0 / UNIT_DIFFUSIVITY_K * 1e-3
        centres, walls = pipe.wall_face_centres, pipe.wall_temperatures_k  # type: ignore[attr-defined]
        for z in (100.0, 120.0):
            wall = float(walls[np.abs(centres[:, 2] - z) < 1.25].mean())
            assert gradient * 10.0 / (wall - _bulk_temperature(pipe, z)) == pytest.approx(48.0 / 11.0, rel=0.025)

    def test_the_wall_heats_along_the_flow(self, pipe: object) -> None:
        heat = pipe.result.heat  # type: ignore[attr-defined]
        assert heat.outlet_bulk_temperature_k > 300.0
        assert heat.wall_max_temperature_k > heat.wall_mean_temperature_k > heat.outlet_bulk_temperature_k - 1.0
        assert heat.wall_max_temperature_k == pytest.approx(float(pipe.wall_temperatures_k.max()))  # type: ignore[attr-defined]


@needs_openfoam
class TestASquareDuctFromATetMesh:
    def test_the_developed_gradient_is_the_series_solution(self) -> None:
        mesh = box_mesh((10.0, 10.0, 120.0), (2, 2, 12))
        output = LaminarFlowSolver().solve(mesh, _case(cell_size_mm=0.5))
        expected = _rectangular_gradient(5.0, 5.0, 1.0, 10.0)
        assert _developed_gradient(output, 1.0, (70.0, 110.0)) == pytest.approx(expected, rel=0.015)
        assert output.result.hydraulic_diameter_mm == pytest.approx(10.0)
        assert output.result.reynolds_number == pytest.approx(100.0)
