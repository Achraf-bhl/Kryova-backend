"""OpenFOAM, federated: laminar flow and forced convection through a duct (master plan E10 task 2).

Reading order: `case.py` (what is asked), `surface.py` (the duct's closed surface,
split into inlet, outlet and wall by selector), `dictionaries.py` (the case as
files), `run.py` (the process boundary — OpenFOAM is GPL and is only ever run as
a separate process), `results.py` (reading what it wrote), `solver.py` (the whole
path, and what is refused before and after it runs).

Scope is deliberately the one the plan names first — cooling flow and ducting:
steady, laminar, incompressible flow, meshed from the part's own geometry with
snappyHexMesh, optionally carrying a passive temperature from an isothermal or
uniform-flux wall. No turbulence model, no buoyancy, no temperature-dependent
properties, no conduction in the solid (the wall condition is given, not
solved), no free surface.
"""

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
from app.solve.openfoam.run import OpenFoamUnavailable
from app.solve.openfoam.solver import ConvectionResult, FlowOutput, FlowResult, LaminarFlowSolver

__all__ = [
    "LAMINAR_REYNOLDS_LIMIT",
    "ConvectionResult",
    "FlowCase",
    "FlowInlet",
    "FlowOutlet",
    "FlowOutput",
    "FlowResult",
    "Fluid",
    "ForcedConvection",
    "LaminarFlowSolver",
    "OpenFoamUnavailable",
    "WallHeatFlux",
    "WallTemperature",
]
