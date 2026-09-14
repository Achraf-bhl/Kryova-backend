"""Laminar internal flow through a duct: mesh + case → OpenFOAM → a pressure drop.

`LaminarFlowSolver.solve(mesh, case)` takes the part's own tet mesh — the fluid
region modelled as a solid, which is how a duct is drawn in CAD — resolves the
inlet and outlet with selectors, writes the surface and the case
(`surface.py`, `dictionaries.py`), runs blockMesh → snappyHexMesh → checkMesh →
simpleFoam (`run.py`), and reads back what the solver integrated over its own
patches (`results.py`).

**Not a `Solver` subclass**, for `ShellSolver`'s reason: the ABC takes a
`LoadCase`, and a flow has none. The output is not a `SolveOutput` either — there
is no displacement and no stress, and a field of zeros in their place would be
read as a part that did not move.

Refused before anything runs, each with the number that would work:

* a Reynolds number above `LAMINAR_REYNOLDS_LIMIT` on the inlet's hydraulic
  diameter — there is no turbulence model;
* fewer than `MIN_CELLS_ACROSS` background cells across that diameter — a
  parabola drawn in three steps is not a velocity profile;
* a background grid over `MAX_BACKGROUND_CELLS`.

Refused after running: a mesh checkMesh fails, and a SIMPLE run that did not meet
its residual targets. **An unconverged pressure drop is not returned**, however
plausible it looks. A converged one is still `single-grid`: one snapped mesh holds
no evidence about its own discretisation error.

**With heat** (`FlowCase.heat`) the run also reports what the temperature did:
the outlet's bulk temperature, the heat the fluid carried, the wall's mean and
hottest temperature, and — for an isothermal wall only — a mean heat-transfer
coefficient on the log-mean temperature difference, which is that condition's
standard definition. A flux wall gets no coefficient, because its definitions
differ by which temperature difference is meant and a number with an unstated
definition is not one an engineer can use; it gets an energy balance instead,
because there the heat put in is known. A temperature that did not settle on the
converged flow is refused like an unconverged pressure.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from app.mesh.types import TetMesh
from app.solve.openfoam.case import LAMINAR_REYNOLDS_LIMIT, FlowCase, WallTemperature
from app.solve.openfoam.dictionaries import (
    COMMANDS,
    ENERGY_RESIDUAL_TARGET,
    TEMPERATURE,
    WALL_SURFACE,
    background_cells,
    case_files,
)
from app.solve.openfoam.results import (
    SolverLog,
    kinematic_to_mpa,
    last_value,
    mesh_ok,
    patch_area,
    read_cell_count,
    read_internal_field,
    read_raw_surface,
    read_solver_log,
)
from app.solve.openfoam.run import DEFAULT_IMAGE, DEFAULT_TIMEOUT_S, run_case
from app.solve.openfoam.surface import INLET, MIN_CELLS_ACROSS, WALL, DuctSurface, duct_surface
from app.solve.types import MeshConvergence, SolverError

#: Background cells above which a run is refused rather than started.
MAX_BACKGROUND_CELLS: Final = 8_000_000

#: Relative mismatch between inflow and outflow above which the result warns.
CONTINUITY_WARNING: Final = 1e-4

#: Relative mismatch between the heat a flux wall put in and the heat the fluid
#: carried out, above which a heated result warns. Measured 0.08% on the test
#: pipe; what is left is heat conducted back out through the inlet.
ENERGY_BALANCE_WARNING: Final = 0.02

#: Below this ratio of outlet to inlet temperature difference, the fluid has left
#: at the wall's temperature and a log-mean coefficient is no longer determined.
_SATURATED: Final = 1e-6

_STEPS: Final = {10 + index: name for index, (name, _) in enumerate(COMMANDS)}


class ConvectionResult(BaseModel):
    """What the temperature carried by a converged flow established."""

    inlet_temperature_k: float
    #: Mixed-mean (flux-weighted) temperature leaving, absolute kelvin.
    outlet_bulk_temperature_k: float
    wall_mean_temperature_k: float
    #: The hottest (or, for a cooled fluid, the coldest-wall question's opposite)
    #: wall face — the largest wall temperature there is.
    wall_max_temperature_k: float
    #: ρ c_p Q (T_out − T_in), W. Positive means the fluid was heated.
    heat_to_fluid_w: float
    #: Area of the snapped mesh's walls, mm² — what a flux was applied over.
    wall_area_mm2: float
    prandtl_number: float
    #: On the inlet's hydraulic diameter and mean velocity.
    peclet_number: float
    #: A flux wall's heat in, q × wall area, W. None for an isothermal wall.
    wall_heat_input_w: float | None = None
    #: |heat_to_fluid − wall_heat_input| / |wall_heat_input|. None where the heat
    #: in is not prescribed, or is zero.
    energy_balance_error: float | None = None
    #: Isothermal wall only: heat_to_fluid / (wall area × ΔT_lm), W/(m²·K).
    mean_heat_transfer_coefficient_w_m2k: float | None = None


class FlowResult(BaseModel):
    """What a converged laminar run established. Small enough for the job row."""

    name: str
    #: Area-averaged inlet pressure minus outlet pressure, MPa.
    pressure_drop_mpa: float
    inlet_flow_rate_mm3_s: float
    outlet_flow_rate_mm3_s: float
    #: |inflow − outflow| / inflow, as the solver integrated them.
    continuity_error: float
    max_velocity_mm_s: float
    mean_inlet_velocity_mm_s: float
    reynolds_number: float
    hydraulic_diameter_mm: float
    inlet_area_mm2: float
    cells: int
    iterations: int
    residuals: dict[str, float]
    solver: str = "openfoam"
    application: str = "simpleFoam"
    solver_version: str | None = None
    launcher: str = "docker"
    image: str | None = None
    mesh_convergence: MeshConvergence = Field(default_factory=MeshConvergence)
    warnings: list[str] = Field(default_factory=list)
    #: Present when the case carried heat.
    heat: ConvectionResult | None = None


@dataclass(frozen=True)
class FlowOutput:
    result: FlowResult
    cell_centres: NDArray[np.float64]  # (n, 3) mm
    velocity: NDArray[np.float64]  # (n, 3) mm/s
    #: Kinematic pressure per cell, mm²/s². Multiply by ρ·1e-12 for MPa.
    kinematic_pressure: NDArray[np.float64]
    #: (n,) mm³. A snapped cell by the wall is smaller than one in the core.
    cell_volumes: NDArray[np.float64]
    #: (n,) absolute kelvin; None without heat.
    temperature_k: NDArray[np.float64] | None = None
    #: (m, 3) mm and (m,) K: every wall face's centre and temperature; None without heat.
    wall_face_centres: NDArray[np.float64] | None = None
    wall_temperatures_k: NDArray[np.float64] | None = None


def check_case(surface: DuctSurface, case: FlowCase) -> tuple[float, float]:
    """Refuse a case this solver would answer wrongly. Returns (D_h, Re)."""
    diameter = surface.hydraulic_diameter(INLET)
    reynolds = case.inlet.mean_velocity_mm_s * diameter / case.fluid.kinematic_viscosity_mm2_s
    if reynolds > LAMINAR_REYNOLDS_LIMIT:
        slowest = LAMINAR_REYNOLDS_LIMIT * case.fluid.kinematic_viscosity_mm2_s / diameter
        raise SolverError(
            f"The inlet Reynolds number is {reynolds:.0f} on a hydraulic diameter of "
            f"{diameter:.4g} mm, above the laminar limit of {LAMINAR_REYNOLDS_LIMIT:.0f}. The flow "
            "may be turbulent there and this solver has no turbulence model, so it would "
            f"converge to a flow that does not exist. Below {slowest:.4g} mm/s it is laminar."
        )
    across = diameter / case.cell_size_mm
    if across < MIN_CELLS_ACROSS:
        raise SolverError(
            f"A cell size of {case.cell_size_mm:g} mm puts {across:.1f} cells across the inlet's "
            f"{diameter:.4g} mm hydraulic diameter; at least {MIN_CELLS_ACROSS} are needed to "
            f"resolve the velocity profile. Use cell_size_mm of {diameter / MIN_CELLS_ACROSS:.4g} or less."
        )
    _, _, counts = background_cells(surface, case.cell_size_mm)
    total = math.prod(counts)
    if total > MAX_BACKGROUND_CELLS:
        scale = (total / MAX_BACKGROUND_CELLS) ** (1.0 / 3.0)
        raise SolverError(
            f"The background grid would hold {total:,} cells, over the limit of "
            f"{MAX_BACKGROUND_CELLS:,}. Raise cell_size_mm to about "
            f"{case.cell_size_mm * scale:.4g} mm, or model a shorter length of duct."
        )
    return diameter, reynolds


class LaminarFlowSolver:
    name = "openfoam"

    def __init__(
        self,
        *,
        launcher: str = "docker",
        image: str = DEFAULT_IMAGE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        keep: str | Path | None = None,
    ) -> None:
        self.launcher = launcher
        self.image = image
        self.timeout_s = timeout_s
        self.keep = Path(keep) if keep is not None else None

    def solve(self, mesh: TetMesh, case: FlowCase) -> FlowOutput:
        return self.solve_surface(duct_surface(mesh, case), case)

    def solve_surface(self, surface: DuctSurface, case: FlowCase) -> FlowOutput:
        diameter, reynolds = check_case(surface, case)
        if self.keep is not None:
            self.keep.mkdir(parents=True, exist_ok=True)
            return self._run(self.keep, surface, case, diameter, reynolds)
        with tempfile.TemporaryDirectory(prefix="kryova-openfoam-") as tmp:
            return self._run(Path(tmp), surface, case, diameter, reynolds)

    def _run(
        self, directory: Path, surface: DuctSurface, case: FlowCase, diameter: float, reynolds: float
    ) -> FlowOutput:
        for relative, text in case_files(case, surface).items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        run = run_case(directory, launcher=self.launcher, image=self.image, timeout_s=self.timeout_s)

        def log(name: str) -> str:
            path = directory / f"log.{name}"
            return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

        if run.returncode != 0:
            step = _STEPS.get(run.returncode)
            if step is None:
                raise SolverError(
                    f"OpenFOAM exited with status {run.returncode} before any step ran: "
                    f"{run.output.strip()[-800:]}"
                )
            tail = "\n".join(log(step).strip().splitlines()[-25:])
            advice = {
                "snappyHexMesh": " The surface must be closed, and the inlet and outlet must be faces of it.",
                "checkMesh": " The snapped mesh failed its quality checks; a smaller cell_size_mm usually fixes it.",
            }.get(step, "")
            raise SolverError(f"OpenFOAM's {step} failed.{advice}\n{tail}")

        checked = log("checkMesh")
        if not mesh_ok(checked):
            raise SolverError(
                "checkMesh did not pass the snapped mesh; a pressure drop on it would be a "
                "number about a broken mesh. Use a smaller cell_size_mm.\n"
                + "\n".join(checked.strip().splitlines()[-25:])
            )
        cells = read_cell_count(checked)
        solved = read_solver_log(log("simpleFoam"))
        if not solved.converged:
            residuals = ", ".join(f"{k} {v:.3g}" for k, v in sorted(solved.residuals.items()))
            raise SolverError(
                f"SIMPLE did not converge in {case.max_iterations} iterations (last initial "
                f"residuals: {residuals or 'none recorded'}). An unconverged pressure drop is not "
                "reported. Raise max_iterations, or check the inlet and outlet are the right faces."
            )
        latest = directory / str(solved.iterations)
        if not latest.is_dir():
            raise SolverError(f"simpleFoam reported convergence at {solved.iterations} but wrote no {latest.name}/ directory.")
        velocity = read_internal_field((latest / "U").read_text(encoding="utf-8"), cells)
        pressure = read_internal_field((latest / "p").read_text(encoding="utf-8"), cells)
        centres = read_internal_field((latest / "C").read_text(encoding="utf-8"), cells)
        volumes = read_internal_field((latest / "V").read_text(encoding="utf-8"), cells)

        def table_text(name: str) -> str:
            path = directory / "postProcessing" / name / "0" / "surfaceFieldValue.dat"
            if not path.is_file():
                raise SolverError(f"The {name} function object wrote nothing; the patch may be empty.")
            return path.read_text(encoding="utf-8")

        def table(name: str) -> float:
            return last_value(table_text(name))

        inflow = -table("inletFlux")
        outflow = table("outletFlux")
        drop = table("inletPressure") - table("outletPressure")
        continuity = abs(inflow - outflow) / abs(inflow) if inflow else float("inf")
        warnings = []
        if continuity > CONTINUITY_WARNING:
            warnings.append(
                f"Inflow and outflow differ by {continuity:.2e} of the inflow; a converged "
                "incompressible run should agree to its solver tolerance."
            )
        heat: ConvectionResult | None = None
        temperature = wall_centres = wall_temperatures = None
        if case.heat is not None:
            temperature = read_internal_field((latest / TEMPERATURE).read_text(encoding="utf-8"), cells)
            raw = directory / "postProcessing" / WALL_SURFACE / "surface" / str(solved.iterations) / f"{TEMPERATURE}_patch_{WALL}.raw"
            if not raw.is_file():
                raise SolverError(f"The wall temperatures were not written ({raw.name} is missing).")
            wall_centres, wall_temperatures = read_raw_surface(raw.read_text(encoding="utf-8"))
            heat = _convection(case, solved, inflow, diameter, table_text, table, warnings)

        result = FlowResult(
            name=case.name,
            pressure_drop_mpa=kinematic_to_mpa(drop, case.fluid.density_kg_m3),
            inlet_flow_rate_mm3_s=inflow,
            outlet_flow_rate_mm3_s=outflow,
            continuity_error=continuity,
            max_velocity_mm_s=float(np.linalg.norm(velocity, axis=1).max()),
            mean_inlet_velocity_mm_s=case.inlet.mean_velocity_mm_s,
            reynolds_number=reynolds,
            hydraulic_diameter_mm=diameter,
            inlet_area_mm2=surface.area_of(INLET),
            cells=cells,
            iterations=solved.iterations,
            residuals=solved.residuals,
            solver_version=solved.version,
            launcher=self.launcher,
            image=self.image if self.launcher == "docker" else None,
            warnings=warnings,
            heat=heat,
        )
        return FlowOutput(
            result=result,
            cell_centres=centres,
            velocity=velocity,
            kinematic_pressure=pressure,
            cell_volumes=volumes,
            temperature_k=temperature,
            wall_face_centres=wall_centres,
            wall_temperatures_k=wall_temperatures,
        )


def _convection(
    case: FlowCase,
    solved: SolverLog,
    inflow_mm3_s: float,
    diameter_mm: float,
    text: Callable[[str], str],
    table: Callable[[str], float],
    warnings: list[str],
) -> ConvectionResult:
    """The heat half of a result, refused if the temperature did not settle."""
    heat = case.heat
    assert heat is not None
    residual = solved.residuals.get(TEMPERATURE)
    if residual is None or residual > ENERGY_RESIDUAL_TARGET:
        raise SolverError(
            f"The temperature did not settle on the converged flow (last initial residual "
            f"{'not recorded' if residual is None else f'{residual:.3g}'}, target "
            f"{ENERGY_RESIDUAL_TARGET:g}), so no temperature from this run is reported."
        )
    density = case.fluid.density_kg_m3
    diffusivity = heat.diffusivity_mm2_s(density)
    inlet = heat.inlet_temperature_k
    outlet = table("outletTemperature")
    area = patch_area(text("wallMeanTemperature"))
    # kg/m³ × J/(kg·K) × mm³/s × K = 1e-9 W: the one conversion in this function.
    carried = density * heat.specific_heat_j_kgk * inflow_mm3_s * (outlet - inlet) * 1e-9

    heat_in: float | None = None
    balance: float | None = None
    coefficient: float | None = None
    if isinstance(heat.wall, WallTemperature):
        wall = heat.wall.temperature_k
        low, high = sorted((inlet, wall))
        if not low - 1e-9 <= outlet <= high + 1e-9:
            warnings.append(
                f"The outlet's bulk temperature, {outlet:.6g} K, lies outside the inlet and wall "
                f"temperatures ({low:.6g}–{high:.6g} K), which no converged heat balance allows. "
                "The mesh is too coarse for the temperature gradients; use a smaller cell_size_mm."
            )
        entering, leaving = wall - inlet, wall - outlet
        if entering == 0.0:
            warnings.append("The wall is at the inlet temperature, so no heat moves and no coefficient exists.")
        elif not 0.0 < leaving / entering < 1.0:
            pass  # the bound warning above already names the problem
        elif leaving / entering < _SATURATED:
            warnings.append(
                "The fluid leaves at the wall's temperature, so the duct is longer than the heat "
                "needed and a mean coefficient over its length is not determined by this run."
            )
        else:
            log_mean = (entering - leaving) / math.log(entering / leaving)
            coefficient = carried / (area * 1e-6 * log_mean)
    else:
        heat_in = heat.wall.flux_w_m2 * area * 1e-6
        if heat_in != 0.0:
            balance = abs(carried - heat_in) / abs(heat_in)
            if balance > ENERGY_BALANCE_WARNING:
                warnings.append(
                    f"The walls put in {heat_in:.4g} W and the fluid carried {carried:.4g} W, "
                    f"{balance:.1%} apart. A converged run balances to well under "
                    f"{ENERGY_BALANCE_WARNING:.0%}; a smaller cell_size_mm usually closes it."
                )
    return ConvectionResult(
        inlet_temperature_k=inlet,
        outlet_bulk_temperature_k=outlet,
        wall_mean_temperature_k=table("wallMeanTemperature"),
        wall_max_temperature_k=table("wallMaxTemperature"),
        heat_to_fluid_w=carried,
        wall_area_mm2=area,
        prandtl_number=case.fluid.kinematic_viscosity_mm2_s / diffusivity,
        peclet_number=case.inlet.mean_velocity_mm_s * diameter_mm / diffusivity,
        wall_heat_input_w=heat_in,
        energy_balance_error=balance,
        mean_heat_transfer_coefficient_w_m2k=coefficient,
    )


__all__ = [
    "CONTINUITY_WARNING",
    "ENERGY_BALANCE_WARNING",
    "ConvectionResult",
    "MAX_BACKGROUND_CELLS",
    "FlowOutput",
    "FlowResult",
    "LaminarFlowSolver",
    "check_case",
]
