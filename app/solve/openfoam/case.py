"""What a laminar internal-flow run is asked, and what it answers.

A sibling of `LoadCase` and `ThermalCase`, for their reason: no fixtures, no
modulus, no conductivity — a fluid, an inlet and an outlet — and a different
answer. The inlet and the outlet are named with the **same selector vocabulary**
the structural and thermal cases use, so "the bottom face" means one thing in all
three; every boundary triangle neither selects is a no-slip wall.

**Units stay mm-N-MPa.** Velocity is mm/s and kinematic viscosity mm²/s — water
at 20 °C is about 1.0 mm²/s, air about 15 — and OpenFOAM is handed those numbers
unchanged: its dimension sets are exponents of *length* and *time*, not metres, so
a case written consistently in millimetres is a consistent case. Pressure leaves
the solver as a kinematic pressure (mm²/s²) and becomes MPa exactly once, in
`results.py`, where the density is applied.

**Heat is optional and passive** (`ForcedConvection`). The temperature is carried
by the converged flow and does not act back on it: no buoyancy, and properties
that do not vary with temperature. That is forced convection in a cooling duct,
and it is the whole of what is claimed — a flow driven by its own heating, or a
fluid whose viscosity halves across the duct, is a different problem this does
not solve. Watts enter here and convert once, the way they do in
`app/solve/conduction.py`: a conductivity in W/(m·K) with a density and a
specific heat becomes a diffusivity in mm²/s, and a wall flux in W/m² becomes a
temperature gradient in K/mm.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

from app.solve.types import Selector

#: Highest Reynolds number accepted, on the inlet's hydraulic diameter. Laminar
#: flow in a duct is dependable below roughly 2000; above it the flow may be
#: transitional or turbulent, and a laminar solve there converges to a number
#: that describes a flow which does not exist. There is no turbulence model here,
#: so the honest answer above the limit is a refusal.
LAMINAR_REYNOLDS_LIMIT: Final = 2000.0


class Fluid(BaseModel):
    """A Newtonian, incompressible fluid at one temperature."""

    name: str = "fluid"
    #: ν, mm²/s. Water ≈ 1.0 at 20 °C; air ≈ 15 at 20 °C; a light oil ≈ 30–100.
    kinematic_viscosity_mm2_s: float = Field(gt=0)
    #: ρ, kg/m³ — the codebase's density unit. It turns the kinematic pressure the
    #: solver reports into MPa and, with heat, a conductivity into a diffusivity;
    #: the flow itself does not depend on it.
    density_kg_m3: float = Field(gt=0)


class FlowInlet(BaseModel):
    """Where fluid enters, with a uniform velocity normal to that face."""

    where: Selector
    #: Mean velocity through the inlet, mm/s, directed into the duct.
    mean_velocity_mm_s: float = Field(gt=0)


class FlowOutlet(BaseModel):
    """Where fluid leaves, at a reference pressure of zero."""

    where: Selector


class WallTemperature(BaseModel):
    """Every wall held at one temperature: an isothermal duct."""

    type: Literal["wall_temperature"] = "wall_temperature"
    #: Absolute kelvin, for the reason `conduction.FixedTemperature` gives.
    temperature_k: float = Field(gt=0)


class WallHeatFlux(BaseModel):
    """One heat flux through every wall, W/m². **Positive heats the fluid.**

    Zero is an insulated duct, and is allowed: the fluid then leaves at the
    temperature it entered, which is a statement worth being able to make.
    """

    type: Literal["wall_heat_flux"] = "wall_heat_flux"
    flux_w_m2: float


WallCondition = Annotated[WallTemperature | WallHeatFlux, Field(discriminator="type")]


class ForcedConvection(BaseModel):
    """A temperature carried by the flow, with every wall under one condition.

    The fluid's thermal properties live here rather than on `Fluid`, for the
    reason conductivity lives on `ThermalCase` and not on `Material`: a flow run
    with no heat needs none of them, and a default nobody chose would reach the
    answer unannounced.
    """

    #: Uniform temperature of the fluid entering, absolute kelvin.
    inlet_temperature_k: float = Field(gt=0)
    wall: WallCondition
    #: k, W/(m·K). Water ≈ 0.60, air ≈ 0.026, a light oil ≈ 0.13.
    conductivity_w_mk: float = Field(gt=0)
    #: c_p, J/(kg·K). Water ≈ 4180, air ≈ 1005, a light oil ≈ 1900.
    specific_heat_j_kgk: float = Field(gt=0)

    def diffusivity_mm2_s(self, density_kg_m3: float) -> float:
        """α = k / (ρ c_p), converted from m²/s to mm²/s — the one factor of 1e6."""
        return self.conductivity_w_mk / (density_kg_m3 * self.specific_heat_j_kgk) * 1e6

    def wall_gradient_k_mm(self) -> float | None:
        """The wall-normal temperature gradient a flux imposes, K/mm, or None.

        q / k is K/m; 1e-3 makes it K/mm. Outward from the fluid, so heat flowing
        *in* (a positive flux) is a temperature that rises towards the wall.
        """
        if isinstance(self.wall, WallHeatFlux):
            return self.wall.flux_w_m2 / self.conductivity_w_mk * 1e-3
        return None


class FlowCase(BaseModel):
    """Steady, laminar, incompressible flow through a duct."""

    name: str = "Laminar internal flow"
    fluid: Fluid
    inlet: FlowInlet
    outlet: FlowOutlet
    #: Background cell size, mm. snappyHexMesh cuts the duct out of a grid of this
    #: size, so it sets the resolution across the smallest passage.
    cell_size_mm: float = Field(gt=0)
    #: SIMPLE iterations allowed before the run is refused as not converged.
    max_iterations: int = Field(default=2000, ge=1, le=100_000)
    #: Carry a temperature through the flow as well. None solves the flow alone.
    heat: ForcedConvection | None = None


__all__ = [
    "LAMINAR_REYNOLDS_LIMIT",
    "FlowCase",
    "FlowInlet",
    "FlowOutlet",
    "Fluid",
    "ForcedConvection",
    "WallCondition",
    "WallHeatFlux",
    "WallTemperature",
]
