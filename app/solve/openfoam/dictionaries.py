"""The OpenFOAM case, as files — written here and nowhere else.

Every dictionary a run needs is produced by one function from the case and the
surface, so a case can be written and inspected on a machine with no OpenFOAM,
which is where this was written. The choices that decide an answer are tables of
constants rather than inline strings, so a measurement that corrects one changes
one value.

What the choices are, and why:

* **`simpleFoam`, `simulationType laminar`.** Steady and incompressible; no
  turbulence model, because `case.LAMINAR_REYNOLDS_LIMIT` refuses the regime that
  would need one.
* **`consistent yes` (SIMPLEC)** so pressure needs no under-relaxation, and the
  residual targets are tight — `p` 1e-5 and `U` 1e-6 initial residual — because a
  pressure drop read from a half-converged field is the unconverged number
  Decision 3 forbids. A run that does not reach them is refused, not reported.
* **Second-order convection (`linearUpwind`) and one non-orthogonal corrector with
  limited corrected 0.5**, because a snapped mesh is not orthogonal next to the
  wall, and that is exactly where the velocity gradient is.
* **Function objects** write the area-averaged pressure on the inlet and the outlet
  and the flux through both, every iteration, so the result is read off the
  solver's own integration over its own patches rather than re-integrated here.
* **Heat is a `scalarTransport` function object**, not a different solver. It
  solves `div(phi T) = div(α grad T)` for the flux SIMPLE has just produced, every
  iteration, to an absolute linear tolerance with no relative stop — so each solve
  is exact for that flux, and the last one, which OpenFOAM runs after "SIMPLE
  solution converged", is exact for the converged flow. Measured on 2412: that
  final solve's initial residual is 3e-10–7e-10, which is how far the flux moved
  in the last iteration. It is the incompressible branch of the object, which
  applies no `bounded01` clipping, so a temperature in kelvin is carried whole.
"""

from __future__ import annotations

import math
from typing import Final

import numpy as np

from app.solve.openfoam.case import FlowCase
from app.solve.openfoam.surface import INLET, OUTLET, WALL, DuctSurface

#: The name snappyHexMesh knows the surface by, and the STL's file name.
SURFACE_NAME: Final = "duct"

#: Initial-residual targets at which SIMPLE counts as converged.
RESIDUAL_TARGETS: Final = {"p": 1e-5, "U": 1e-6}

#: The temperature solve's initial residual on the converged flow, above which a
#: heated run is refused. Not in SIMPLE's `residualControl`: the temperature does
#: not act on the flow, so it has no business deciding when the flow has settled.
ENERGY_RESIDUAL_TARGET: Final = 1e-6

#: The transported temperature's field name.
TEMPERATURE: Final = "T"

#: Background-grid margin around the surface, in cells.
MARGIN_CELLS: Final = 2

#: Printed at the top of every file so a case directory says what wrote it.
_BANNER: Final = "// Written by Kryova (app/solve/openfoam/dictionaries.py).\n"


def _header(cls: str, obj: str) -> str:
    return (
        f"{_BANNER}FoamFile\n{{\n    version     2.0;\n    format      ascii;\n"
        f"    class       {cls};\n    object      {obj};\n}}\n\n"
    )


def _number(value: float) -> str:
    # `+ 0.0` turns −0.0 into 0.0: a velocity written as (-0 -0 10) reads the same
    # to OpenFOAM and differently to every person comparing two case files.
    return f"{value + 0.0:.12g}"


def _vector(values: np.ndarray | tuple[float, float, float]) -> str:
    return "(" + " ".join(_number(float(v)) for v in values) + ")"


def background_cells(surface: DuctSurface, cell_size_mm: float) -> tuple[tuple[float, ...], tuple[float, ...], tuple[int, int, int]]:
    """The background grid: its corners and its cell counts."""
    corners = surface.nodes[np.unique(surface.triangles)]
    low = corners.min(axis=0) - MARGIN_CELLS * cell_size_mm
    high = corners.max(axis=0) + MARGIN_CELLS * cell_size_mm
    counts = tuple(max(1, math.ceil(float(span) / cell_size_mm)) for span in high - low)
    high = low + np.asarray(counts, dtype=np.float64) * cell_size_mm
    return tuple(map(float, low)), tuple(map(float, high)), (counts[0], counts[1], counts[2])


def block_mesh_dict(surface: DuctSurface, cell_size_mm: float) -> str:
    low, high, (nx, ny, nz) = background_cells(surface, cell_size_mm)
    x0, y0, z0 = low
    x1, y1, z1 = high
    vertices = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    listed = "\n".join(f"    {_vector(v)}" for v in vertices)
    return _header("dictionary", "blockMeshDict") + (
        "scale 1;\n\n"
        f"vertices\n(\n{listed}\n);\n\n"
        f"blocks\n(\n    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)\n);\n\n"
        "boundary\n(\n    background\n    {\n        type patch;\n"
        "        faces ((0 3 2 1) (4 5 6 7) (0 1 5 4) (1 2 6 5) (2 3 7 6) (3 0 4 7));\n    }\n);\n"
    )


def snappy_hex_mesh_dict(surface: DuctSurface) -> str:
    patches = "\n".join(
        f"                {name} {{ level (0 0); patchInfo {{ type {kind}; }} }}"
        for name, kind in ((INLET, "patch"), (OUTLET, "patch"), (WALL, "wall"))
    )
    regions = " ".join(f"{name} {{ name {name}; }}" for name in (INLET, OUTLET, WALL))
    return _header("dictionary", "snappyHexMeshDict") + (
        "castellatedMesh true;\nsnap true;\naddLayers false;\n\n"
        f"geometry\n{{\n    {SURFACE_NAME}\n    {{\n        type triSurfaceMesh;\n"
        f"        file \"{SURFACE_NAME}.stl\";\n        regions {{ {regions} }}\n    }}\n}};\n\n"
        "castellatedMeshControls\n{\n"
        "    maxLocalCells 5000000;\n    maxGlobalCells 10000000;\n    minRefinementCells 0;\n"
        "    maxLoadUnbalance 0.1;\n    nCellsBetweenLevels 2;\n    features ();\n"
        f"    refinementSurfaces\n    {{\n        {SURFACE_NAME}\n        {{\n"
        f"            level (0 0);\n            regions\n            {{\n{patches}\n            }}\n"
        "        }\n    }\n"
        "    resolveFeatureAngle 30;\n    refinementRegions {}\n"
        f"    locationInMesh {_vector(surface.inside)};\n"
        "    allowFreeStandingZoneFaces true;\n}\n\n"
        "snapControls\n{\n    nSmoothPatch 3;\n    tolerance 2.0;\n    nSolveIter 50;\n"
        "    nRelaxIter 5;\n    nFeatureSnapIter 10;\n    implicitFeatureSnap true;\n"
        "    explicitFeatureSnap false;\n    multiRegionFeatureSnap false;\n}\n\n"
        "addLayersControls\n{\n    relativeSizes true;\n    layers {}\n    expansionRatio 1.0;\n"
        "    finalLayerThickness 0.3;\n    minThickness 0.1;\n    nGrow 0;\n    featureAngle 60;\n"
        "    nRelaxIter 3;\n    nSmoothSurfaceNormals 1;\n    nSmoothNormals 3;\n"
        "    nSmoothThickness 10;\n    maxFaceThicknessRatio 0.5;\n"
        "    maxThicknessToMedialRatio 0.3;\n    minMedialAxisAngle 90;\n"
        "    nBufferCellsNoExtrude 0;\n    nLayerIter 50;\n}\n\n"
        "meshQualityControls\n{\n    #includeEtc \"caseDicts/meshQualityDict\"\n"
        "    nSmoothScale 4;\n    errorReduction 0.75;\n}\n\n"
        "mergeTolerance 1e-6;\n"
    )


def _surface_value(name: str, patch: str, operation: str, field: str, extra: str = "") -> str:
    return (
        f"    {name}\n    {{\n        type surfaceFieldValue;\n        libs (fieldFunctionObjects);\n"
        "        writeControl timeStep;\n        writeInterval 1;\n        log false;\n"
        f"        writeFields false;\n        regionType patch;\n        name {patch};\n"
        f"        operation {operation};\n{extra}        fields ({field});\n    }}\n"
    )


#: The function objects `results.py` reads, by directory name.
FUNCTION_OBJECTS: Final = {
    "inletPressure": (INLET, "areaAverage", "p"),
    "outletPressure": (OUTLET, "areaAverage", "p"),
    "inletFlux": (INLET, "sum", "phi"),
    "outletFlux": (OUTLET, "sum", "phi"),
}


#: The temperature function objects, read only on a heated run. The outlet's is
#: weighted by the flux, so it is the **mixed-mean** (bulk) temperature — the one
#: an energy balance uses — and not the area average, which over-weights the slow
#: fluid by the wall.
HEAT_FUNCTION_OBJECTS: Final = {
    "outletTemperature": (OUTLET, "weightedAverage", TEMPERATURE, "        weightField phi;\n"),
    "wallMeanTemperature": (WALL, "areaAverage", TEMPERATURE, ""),
    "wallMaxTemperature": (WALL, "max", TEMPERATURE, ""),
}

#: The function object that writes every wall face's temperature once, at the end.
WALL_SURFACE: Final = "wallTemperatureSurface"


def _energy(case: FlowCase) -> str:
    assert case.heat is not None
    diffusivity = case.heat.diffusivity_mm2_s(case.fluid.density_kg_m3)
    # First in the list, because function objects execute in the order written
    # and every temperature table below reads the field this one has just solved.
    return (
        "    energy\n    {\n        type scalarTransport;\n        libs (solverFunctionObjects);\n"
        f"        field {TEMPERATURE};\n        phi phi;\n        D {_number(diffusivity)};\n"
        "        nCorr 0;\n        writeControl writeTime;\n    }\n"
    )


def _wall_surface() -> str:
    return (
        f"    {WALL_SURFACE}\n    {{\n        type surfaceFieldValue;\n        libs (fieldFunctionObjects);\n"
        "        writeControl writeTime;\n        log false;\n        writeFields true;\n"
        f"        surfaceFormat raw;\n        regionType patch;\n        name {WALL};\n"
        f"        operation areaAverage;\n        fields ({TEMPERATURE});\n    }}\n"
    )


def control_dict(case: FlowCase) -> str:
    functions = "".join(
        _surface_value(name, patch, operation, field)
        for name, (patch, operation, field) in FUNCTION_OBJECTS.items()
    )
    if case.heat is not None:
        functions = (
            _energy(case)
            + functions
            + "".join(
                _surface_value(name, patch, operation, field, extra)
                for name, (patch, operation, field, extra) in HEAT_FUNCTION_OBJECTS.items()
            )
            + _wall_surface()
        )
    return _header("dictionary", "controlDict") + (
        "application simpleFoam;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\n"
        f"endTime {case.max_iterations};\ndeltaT 1;\nwriteControl timeStep;\n"
        f"writeInterval {case.max_iterations};\npurgeWrite 0;\nwriteFormat ascii;\n"
        "writePrecision 12;\nwriteCompression off;\ntimeFormat general;\ntimePrecision 8;\n"
        f"runTimeModifiable false;\n\nfunctions\n{{\n{functions}}}\n"
    )


def fv_schemes(heat: bool = False) -> str:
    # `bounded` subtracts div(phi) times the field, which is zero on a converged
    # incompressible flow and keeps a half-converged one from creating heat.
    temperature = f"    div(phi,{TEMPERATURE}) bounded Gauss linearUpwind grad({TEMPERATURE});\n" if heat else ""
    return _header("dictionary", "fvSchemes") + (
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes\n{\n    default none;\n    div(phi,U) bounded Gauss linearUpwind grad(U);\n"
        f"{temperature}"
        "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n"
        "laplacianSchemes { default Gauss linear limited corrected 0.5; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default limited corrected 0.5; }\n"
    )


def fv_solution(heat: bool = False) -> str:
    # relTol 0 for the temperature: see the module docstring.
    temperature = (
        f"    {TEMPERATURE} {{ solver PBiCGStab; preconditioner DILU; tolerance 1e-11; relTol 0; }}\n"
        if heat
        else ""
    )
    return _header("dictionary", "fvSolution") + (
        "solvers\n{\n"
        "    p { solver GAMG; tolerance 1e-9; relTol 0.01; smoother GaussSeidel; }\n"
        "    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0.01; }\n"
        f"{temperature}"
        "}\n\n"
        "SIMPLE\n{\n    nNonOrthogonalCorrectors 1;\n    consistent yes;\n"
        f"    residualControl {{ p {_number(RESIDUAL_TARGETS['p'])}; U {_number(RESIDUAL_TARGETS['U'])}; }}\n"
        "}\n\n"
        "relaxationFactors { equations { U 0.9; } }\n"
    )


def transport_properties(case: FlowCase) -> str:
    return _header("dictionary", "transportProperties") + (
        f"transportModel Newtonian;\nnu {_number(case.fluid.kinematic_viscosity_mm2_s)};\n"
    )


def turbulence_properties() -> str:
    return _header("dictionary", "turbulenceProperties") + "simulationType laminar;\n"


def initial_velocity(case: FlowCase, surface: DuctSurface) -> str:
    inflow = surface.inward_direction(INLET) * case.inlet.mean_velocity_mm_s
    return _header("volVectorField", "U") + (
        "dimensions [0 1 -1 0 0 0 0];\n\ninternalField uniform (0 0 0);\n\n"
        "boundaryField\n{\n"
        f"    {INLET} {{ type fixedValue; value uniform {_vector(inflow)}; }}\n"
        f"    {OUTLET} {{ type zeroGradient; }}\n"
        f"    {WALL} {{ type noSlip; }}\n"
        "    background { type noSlip; }\n"
        "}\n"
    )


def initial_pressure() -> str:
    return _header("volScalarField", "p") + (
        "dimensions [0 2 -2 0 0 0 0];\n\ninternalField uniform 0;\n\n"
        "boundaryField\n{\n"
        f"    {INLET} {{ type zeroGradient; }}\n"
        f"    {OUTLET} {{ type fixedValue; value uniform 0; }}\n"
        f"    {WALL} {{ type zeroGradient; }}\n"
        "    background { type zeroGradient; }\n"
        "}\n"
    )


def initial_temperature(case: FlowCase) -> str:
    assert case.heat is not None
    heat = case.heat
    gradient = heat.wall_gradient_k_mm()
    wall = (
        f"type fixedGradient; gradient uniform {_number(gradient)};"
        if gradient is not None
        else f"type fixedValue; value uniform {_number(heat.wall.temperature_k)};"  # type: ignore[union-attr]
    )
    inlet = _number(heat.inlet_temperature_k)
    return _header("volScalarField", TEMPERATURE) + (
        f"dimensions [0 0 0 1 0 0 0];\n\ninternalField uniform {inlet};\n\n"
        "boundaryField\n{\n"
        f"    {INLET} {{ type fixedValue; value uniform {inlet}; }}\n"
        f"    {OUTLET} {{ type zeroGradient; }}\n"
        f"    {WALL} {{ {wall} }}\n"
        "    background { type zeroGradient; }\n"
        "}\n"
    )


#: The commands a run executes, in order, each with its own log.
COMMANDS: Final = (
    ("blockMesh", "blockMesh"),
    ("snappyHexMesh", "snappyHexMesh -overwrite"),
    ("checkMesh", "checkMesh"),
    ("simpleFoam", "simpleFoam"),
    ("writeCellCentres", "postProcess -func writeCellCentres -latestTime"),
    # A snapped cell by the wall is not the size of one in the core, so any
    # integral over cells — a bulk temperature across a section — needs these.
    ("writeCellVolumes", "postProcess -func writeCellVolumes -latestTime"),
)


def run_script() -> str:
    """`Allrun`: stop at the first command that fails, and say which by exit code."""
    lines = ["#!/bin/bash", 'cd "$(dirname "$0")" || exit 2']
    for index, (name, command) in enumerate(COMMANDS):
        lines.append(f"{command} > log.{name} 2>&1 || exit {10 + index}")
    return "\n".join(lines) + "\n"


def case_files(case: FlowCase, surface: DuctSurface) -> dict[str, str]:
    """Every file of the case directory, by relative path."""
    heat = case.heat is not None
    files = {
        "Allrun": run_script(),
        f"constant/triSurface/{SURFACE_NAME}.stl": surface.stl(),
        "constant/transportProperties": transport_properties(case),
        "constant/turbulenceProperties": turbulence_properties(),
        "system/blockMeshDict": block_mesh_dict(surface, case.cell_size_mm),
        "system/snappyHexMeshDict": snappy_hex_mesh_dict(surface),
        "system/controlDict": control_dict(case),
        "system/fvSchemes": fv_schemes(heat),
        "system/fvSolution": fv_solution(heat),
        "0/U": initial_velocity(case, surface),
        "0/p": initial_pressure(),
    }
    if heat:
        files[f"0/{TEMPERATURE}"] = initial_temperature(case)
    return files


__all__ = [
    "COMMANDS",
    "ENERGY_RESIDUAL_TARGET",
    "FUNCTION_OBJECTS",
    "HEAT_FUNCTION_OBJECTS",
    "RESIDUAL_TARGETS",
    "SURFACE_NAME",
    "TEMPERATURE",
    "WALL_SURFACE",
    "background_cells",
    "case_files",
    "run_script",
]
