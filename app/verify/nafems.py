"""The NAFEMS standard benchmarks, encoded. Master plan 7.1.

`benchmarks.py` decided what a benchmark *is* and deliberately held no
instances. This module holds the instances: the published problems, their
published answers, and — for the ones this codebase cannot yet compute — the
named reason it cannot.

## Where the numbers come from, and the rule that governs them

The NAFEMS publications themselves are not free. **The route the plan names is
the one used here**: the reference values are reproduced, with the NAFEMS test
number cited, in vendor verification manuals that are publicly readable. Each
target's `source` gives the manual, the section, the NAFEMS publication it
credits, the URL and the date it was read, so a reviewer can open the same page.

**A number that was not read off a document does not go in.** `Target` requires
a `source` for anything but `TargetBasis.UNKNOWN`, and `register.py` republishes
that string to an audience outside this repository — so a remembered figure
carrying a plausible citation is indistinguishable, in the published register,
from one somebody checked. That is the single worst thing this module could
contain, and it is worth more than the case: a benchmark with an `UNKNOWN`
target is fully encoded, runs where it can, reports `MEASURED`, and is honestly
not validated. `MEASURED` is never a pass.

**One case is a reminder that this rule earns its keep.** Two vendor manuals
disagree about FV52's fundamental flexural frequency — one reproduces 44.092 Hz
and another quotes 45.897 Hz for what it also calls mode 4. The disagreement is
recorded on the case rather than resolved by preference; the target cites the
manual whose whole reference row was read verbatim and whose own element
reproduces every mode in it to 0.00%. Our own answer converges through 44.10 to
43.62, which is evidence and not arbitration.

## What the catalogue reports today

Three cases of five run, and all three validate — FV52 for free vibration, LE10
for linear static on a solid, and LE1 for linear static in plane stress. The
ratio is the useful output, not an embarrassment: `blockers()` rolls the other
two up by what is missing, and the answer is now only two things — a shape
nobody has authored, and shell elements. A catalogue that reports "this case is
waiting on an element family E6 already describes and nothing solves" is telling
you what to build next, where a silent skip tells you nothing.

**Every blocker here has an owner outside this file**, and that is a rule rather
than a coincidence. For a day, four cases named capabilities that appeared in no
task anywhere in the plan, which reads as diligence and is a silent gap: the
catalogue was describing work nobody was going to do. Plane stress and solving
for a temperature field became master-plan tasks (E7.5, E7.6), and the shell
solver went into `docs/WINDOWS_VERIFICATION.md` because it needs a machine this
one is not. Before recording a blocker, ask whose it is.

**Getting the second one to run cost two changes outside this package, and both
were defects rather than conveniences.** `SolveOutput` gained a nodal stress
tensor, because a stress *at a point* read from an element centroid is a 25%
under-read on a plate in bending — and it shrinks with refinement, so it reads
as a converging answer rather than as an offset. And the region vocabulary
gained `EllipticalWallSelector`, because nothing in it could name a curved outer
edge: a box takes the material inside it, a face takes a plane the wall is not.
A benchmark is worth having partly because it will not accept an approximation
of the model it specifies.

The blocked cases still carry their published targets. Knowing the number we
must eventually produce is most of the value of a benchmark; being unable to
produce it yet is a schedule fact, not a reason to leave the number out.

**The NAFEMS thermal family is absent rather than blocked**, and the distinction
is deliberate. Those tests solve *for* a temperature field, and no analysis here
computes one — `app/solve/thermal.py` applies a uniform temperature change as a
load. A case cannot be blocked on an analysis the product does not claim to
have; that hole belongs in `register.ANALYSES`, whose job is the denominator.
LE11 is in the catalogue as thermal *stress*, which is the part this codebase
does do.

## The one seam to edit when a document arrives

`SOURCES` and the `Target` on each case. Filling in a target is a data edit —
change `TargetBasis.UNKNOWN` to `PUBLISHED`, add the value, the tolerance, the
justification for the tolerance and the source — and nothing else in the module
moves. `docs/WINDOWS_VERIFICATION.md` carries this as a queued item for whoever
has the publications.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from app.mesh.planar import TriMesh
from app.mesh.primitives import box_mesh, promote_to_tet10
from app.mesh.types import MeshError, TetMesh
from app.solve.linear_static import LinearStaticSolver
from app.solve.modal import ModalEigenSolver
from app.solve.plane import PlaneCase, PlaneSolver, PlaneState
from app.solve.types import (
    BoxSelector,
    EllipticalWallSelector,
    FaceSelector,
    Fixture,
    ForceLoad,
    LoadCase,
    Material,
    ModalCase,
    PressureLoad,
)
from app.verify.benchmarks import (
    Benchmark,
    BenchmarkRun,
    Suite,
    Target,
    TargetBasis,
)
from app.verify.convergence import ConvergenceStudy, run_study
from app.verify.le11_geometry import (
    LE11_OUTER_CYLINDER_RADIUS_MM,
    LE11_OUTER_SPHERE_RADIUS_MM,
    LE11_TOTAL_HEIGHT_MM,
    LE11_VOLUME_MM3,
    le11_solid,
)
from app.verify.le11_geometry import LE11_POINT_A as LE11_GEOMETRY_POINT_A
from app.verify.provenance import RunProvenance, identify_solver
from app.verify.quantities import modal_frequency, stress_component_at

# -- sources -----------------------------------------------------------------
#
# Written once each, because a citation repeated inline is a citation that will
# eventually be repeated wrongly. Each names the manual, the section, the NAFEMS
# publication it credits, and the date it was read.

SOURCES: Final[dict[str, str]] = {
    "abaqus-le1": (
        "Abaqus Benchmarks Guide (2017), LE1 'Elliptic membrane under a uniformly "
        "distributed load', reproducing NAFEMS publication TNSB Rev. 3, 'The Standard "
        "NAFEMS Benchmarks', October 1990. Read at "
        "https://abaqus-docs.mit.edu/2017/English/SIMACAEBMKRefMap/simabmk-c-le1.htm "
        "on 2026-09-08."
    ),
    "feenox-le1-geometry": (
        "FeenoX (Seamplex, GPL-3.0) `examples/nafems-le1.geo`, the Gmsh input for its "
        "NAFEMS LE1 verification case, which states the geometry the reproducing "
        "manuals omit: a = 1000, b = 2750, c = 3250, d = 2000 (mm), with "
        "A = (0, a), B = (0, b), C = (c, 0), D = (d, 0), the outer boundary "
        "`Ellipse(0,0,0, c, b, 0, Pi/2)` and the inner `Ellipse(0,0,0, d, a, 0, Pi/2)`. "
        "Read at "
        "https://raw.githubusercontent.com/seamplex/feenox/main/examples/nafems-le1.geo "
        "on 2026-09-08."
    ),
    "altair-le1": (
        "Altair OptiStruct verification problem OS-V: 0010 'Elliptic Membrane', "
        "reproducing NAFEMS publication TNSB Rev. 3, October 1990, which states the "
        "thickness ('thin plate of thickness 0.1m') that the Abaqus entry omits. Read "
        "at https://2022.help.altair.com/2022/hwsolvers/os/topics/solvers/os/"
        "nafems_test_problem_le1_r.htm on 2026-09-08."
    ),
    "abaqus-le3": (
        "Abaqus Benchmarks Guide (2017), LE3 'Hemisphere — point loads', reproducing "
        "NAFEMS publication TNSB Rev. 3, October 1990. Read at "
        "https://abaqus-docs.mit.edu/2017/English/SIMACAEBMKRefMap/simabmk-c-le3.htm "
        "on 2026-09-08."
    ),
    "abaqus-le10": (
        "Abaqus Benchmarks Guide (2017), LE10 'Thick plate under pressure', "
        "reproducing NAFEMS publication TNSB Rev. 3, October 1990. Read at "
        "https://abaqus-docs.mit.edu/2017/English/SIMACAEBMKRefMap/simabmk-c-le10.htm "
        "on 2026-09-08."
    ),
    "esrd-le11-geometry": (
        "ESRD 'Benchmarks Guide — The Standard NAFEMS Benchmarks: Linear Elastic Tests' "
        "(2018), section 'NAFEMS LE11: Solid Cylinder/Taper/Sphere - Temperature Loading', "
        "page 32, reproducing NAFEMS publication TNSB Rev. 3, 'The Standard NAFEMS "
        "Benchmarks', October 1990. The primary geometry source: it reprints the original "
        "dimensioned figure, which the other reproducing manuals omit — inner sphere radius "
        "1.0 m, outer sphere radius 1.4 m, inner cylinder radius 0.7071 m, outer cylinder "
        "radius 0.7071 + 0.2929 = 1.0 m, 45 deg to the inner sphere/cylinder junction, "
        "axial bands 0.700 (see note) + 0.345 + 0.345 + 0.400 m, point A at radius 1.0 m on "
        "the base plane with 0.4 m from A to the outer edge. Read at "
        "https://www.esrd.com/wp-content/uploads/dlm_uploads/"
        "Benchmarks-Guide-Standard-NAFEMS-Benchmarks-Linear-Elastic-Tests.pdf "
        "on 2026-09-09."
    ),
    "feenox-le11-geometry": (
        "FeenoX (Seamplex, GPL-3.0) `examples/nafems-le11.geo`, the Gmsh input for its "
        "NAFEMS LE11 verification case, which fixes what the dimensioned figure leaves "
        "implicit: a 90 deg revolve about z, both arcs centred on the origin, the taper as "
        "one straight segment, and the spherical band's height as 1.000*Sin(Pi/4) rather "
        "than the figure's rounded 0.700 m. Names Point(2) = {1.000, 0, 0} as A. Read at "
        "https://raw.githubusercontent.com/seamplex/feenox/main/examples/nafems-le11.geo "
        "on 2026-09-09."
    ),
    "feenox-le11-model": (
        "FeenoX (Seamplex, GPL-3.0) `examples/nafems-le11.fee`, the solver input for its "
        "NAFEMS LE11 verification case: T(x,y,z) = sqrt(x^2 + y^2) + z with the mesh in "
        "metres, E = 210e3 MPa, nu = 0.3, alpha = 2.3e-4 /degC, w = 0 on both the xy plane "
        "and the face HIH'I', u = 0 on yz, v = 0 on xz, and the target read as "
        "sigmaz(1,0,0). Reports sigma_z(A) = -105.04 MPa against the -105 MPa reference. "
        "Read at "
        "https://raw.githubusercontent.com/seamplex/feenox/main/examples/nafems-le11.fee "
        "and https://www.seamplex.com/feenox/examples/mechanical.html on 2026-09-09."
    ),
    "featool-le11-geometry": (
        "FEATool Multiphysics documentation, 'Temperature Loading of a Tapered Cylinder' "
        "(NAFEMS LE11), the independent geometry cross-check: meridian rectangle "
        "r in [0.7071, 1.4] over an axial extent of 1.79 m, circles of radius 1 and 1.4 "
        "centred on the origin, taper polygon through (0.7071, 0.7), (1.2124, 0.7), "
        "(1, 1.39), (1, 1.79), (0.7071, 1.79), revolved 90 deg; E = 210e9 Pa, nu = 0.3, "
        "alpha = 2.3e-4, T = sqrt(x^2+y^2)+z, reference -105e6 Pa. Read at "
        "https://www.featool.com/doc/Structural_Mechanics_07_temperature_loading1 "
        "on 2026-09-09."
    ),
    "altair-le11": (
        "Altair OptiStruct verification problem OS-V: 0070 'Solid Cylinder/Taper/Sphere - "
        "Temperature', reproducing NAFEMS publication TNSB Rev. 3, October 1990. Carries "
        "the material (210 x 10^3 MPa, 0.3, 2.3 x 10^-4 /degC), the temperature field "
        "T degC = (x2 + y2)1/2 + z and the -105 MPa target, but no dimensions. Read at "
        "https://2021.help.altair.com/2021/hwsolvers/os/topics/solvers/os/"
        "nafems_test_problem_le11_r.htm on 2026-09-09."
    ),
    "abaqus-le11": (
        "Abaqus Benchmarks Guide (2017), LE11 'Solid cylinder/taper/sphere-"
        "temperature', reproducing NAFEMS publication TNSB Rev. 3, October 1990. Read "
        "at "
        "https://abaqus-docs.mit.edu/2017/English/SIMACAEBMKRefMap/simabmk-c-le11.htm "
        "on 2026-09-08."
    ),
    "feenox-le10": (
        "FeenoX / Fino verification case 012, 'NAFEMS LE10 thick plate pressure "
        "benchmark' — the source of the model *dimensions*, which the Abaqus page above "
        "does not give: outer semi-axes 3250 x 2750 mm, inner 2000 x 1000 mm, 600 mm "
        "thick, target sigma_y = -5.38 MPa at D = (2000, 0, 600) mm. Read at "
        "https://www.seamplex.com/fino/cases/012-nafems-le10/ on 2026-09-08."
    ),
    "abaqus-fv52": (
        "Abaqus Benchmarks Guide (2017), FV52 'Simply supported \"solid\" square "
        "plate', reproducing NAFEMS publication TNSB Rev. 3, 'The Standard NAFEMS "
        "Benchmarks', October 1990. Reference row read verbatim — modes 1-3 rigid "
        "body, then 44.092, 106.66, 106.66, 156.23, 193.58, 200.13, 200.13 Hz — at "
        "https://abaqus-docs.mit.edu/2017/English/SIMACAEBMKRefMap/simabmk-c-fv52.htm "
        "on 2026-09-08."
    ),
}


# -- what stops a case running -----------------------------------------------


class Blocker(StrEnum):
    """Why a catalogued case does not execute here.

    An enum rather than free text so `blockers()` can group cases by what would
    release them. Three cases blocked on one missing element family is a
    different message from three cases blocked on three different things, and
    free-text reasons cannot tell those apart.
    """

    #: `app/mesh/structural.py` and `app/solve/sections.py` describe shells, and
    #: `app/solve/calculix/elements.py` writes them into a deck — but nothing
    #: solves one here and no mesher produces one. E6's named residual.
    NO_SHELL_SOLVER = "no-shell-solver"


#: What each blocker means and what would clear it. Kept beside the enum so a
#: report can print the sentence without every call site inventing its own.
BLOCKER_DETAIL: Final[dict[Blocker, str]] = {
    Blocker.NO_SHELL_SOLVER: (
        "The benchmark is posed on shell elements. `app.mesh.structural.ShellMesh` "
        "and `app.solve.sections.ShellSection` describe one and "
        "`app.solve.calculix.elements` writes one into a deck, but no solver here "
        "takes one and no mesher produces one. E6's named residual."
    ),
}


@dataclass(frozen=True)
class Case:
    """One catalogued benchmark, with the blocker that stops it if it is stopped.

    The blocker lives here rather than parsed back out of
    `Benchmark.blocked_reason`, and the suite is built *from* these, so the two
    cannot drift: a case that names a blocker and a case that runs are the same
    object seen twice.
    """

    benchmark: Benchmark
    blocker: Blocker | None = None

    def __post_init__(self) -> None:
        if self.benchmark.runnable and self.blocker is not None:
            raise ValueError(
                f"Case {self.benchmark.id!r} runs and also names a blocker. A blocker "
                "on a case that executes is a note nobody will ever delete."
            )
        if not self.benchmark.runnable and self.blocker is None:
            raise ValueError(
                f"Case {self.benchmark.id!r} does not run and names no blocker. "
                "`blockers()` groups on it, so an unclassified case is invisible in "
                "the one report this catalogue exists to produce."
            )


def blocked(blocker: Blocker, specific: str) -> str:
    """The `blocked_reason` for a case: what is specific to it, then the family.

    The case-specific sentence comes first because it is the one a reader needs;
    the shared explanation follows so a report of five blocked cases still says
    five different things at the top of each line.
    """
    return f"{specific} {BLOCKER_DETAIL[blocker]}"


# -- FV52: simply supported "solid" square plate -----------------------------
#
# The one case that runs. A 10 m square plate 1 m thick, held only out of plane
# along the four edges of its underside — so the plate can still slide in x and
# y and spin about z, and the first three modes are rigid-body ones. That is
# NAFEMS's own model and it is what makes mode *4* the fundamental flexural
# frequency; a reader who assumed mode 1 would compare against a zero.

FV52_SIDE_MM: Final = 10_000.0
FV52_THICKNESS_MM: Final = 1_000.0

#: Half-width of the strip that picks up an edge of the underside, in mm. The
#: nodes sit exactly on the planes, so this only has to survive the arithmetic
#: `numpy.linspace` does at the endpoints — it is not a modelling choice.
_EDGE_BAND_MM: Final = 1.0

#: In-plane divisions per grid level. Doubling and tripling the coarse grid,
#: with the through-thickness count scaled by the same factor, so the three
#: meshes are geometrically similar refinements of one family — which is what
#: the Grid Convergence Index assumes and what makes the observed order mean
#: something. Three levels because that is the minimum GCI needs.
FV52_DIVISIONS: Final[tuple[int, ...]] = (4, 8, 12)

#: The material as the benchmark states it: E = 200 GPa, nu = 0.3,
#: rho = 8000 kg/m^3. `yield_strength_mpa` is required by `Material` and is
#: **not** part of the benchmark — a modal solve never reads it. It is set to a
#: plain structural-steel figure and named as unused rather than left to look
#: like part of the specification.
FV52_MATERIAL: Final = Material(
    name="NAFEMS FV52 plate (E=200 GPa, nu=0.3, rho=8000 kg/m3)",
    youngs_modulus_mpa=200_000.0,
    poissons_ratio=0.3,
    yield_strength_mpa=250.0,
    density_kg_m3=8000.0,
)


def fv52_fixtures() -> list[Fixture]:
    """`uz = 0` along all four edges of the underside, and nothing else.

    Four strips rather than one selector because the four edges of a face are
    not any one region this vocabulary can name: a face selector would take the
    whole underside and clamp the plate flat, which is a different structure
    with no rigid-body modes and a much higher first frequency.
    """
    band = _EDGE_BAND_MM
    side = FV52_SIDE_MM
    strips: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = (
        ((-band, -band, -band), (band, side + band, band)),
        ((side - band, -band, -band), (side + band, side + band, band)),
        ((-band, -band, -band), (side + band, band, band)),
        ((-band, side - band, -band), (side + band, side + band, band)),
    )
    return [
        Fixture(
            where=BoxSelector(min=low, max=high),
            kind="roller",
            normal="z",
            name=f"underside edge {index + 1}",
        )
        for index, (low, high) in enumerate(strips)
    ]


def fv52_case() -> ModalCase:
    """Eight modes: three rigid-body, then the five the reference row names."""
    return ModalCase(
        name="NAFEMS FV52 simply supported solid square plate",
        material=FV52_MATERIAL,
        fixtures=fv52_fixtures(),
        modes=8,
    )


def fv52_mesh(divisions: int) -> TetMesh:
    """The plate at one refinement level, as tet10.

    Quadratic on purpose. tet4 is a constant-strain element and far too stiff in
    bending; a plate benchmark solved with it measures the element, not the
    plate. The through-thickness count is `divisions // 4` so that refining is a
    uniform scaling of all three edges rather than a refinement in plan only.
    """
    if divisions < 4:
        raise ValueError(
            "FV52 needs at least four divisions in plan; below that the "
            "through-thickness count rounds to zero and the mesh is a membrane."
        )
    through = max(1, divisions // 4)
    return promote_to_tet10(
        box_mesh(
            (FV52_SIDE_MM, FV52_SIDE_MM, FV52_THICKNESS_MM),
            (divisions, divisions, through),
        )
    )


#: The quantity: the fourth frequency, zero-based index 3. Named for what it is
#: rather than for its index, because "mode 4" only means the fundamental
#: flexural mode once you know three rigid-body modes come first.
FV52_QUANTITY: Final = modal_frequency(
    3, name="fundamental flexural frequency (mode 4, after three rigid-body modes)"
)


def run_fv52() -> BenchmarkRun:
    """Solve FV52 over three grids and return the converged answer.

    Returns a `BenchmarkRun` whose `value` is the convergence study's
    `stated_value`, which is `None` unless the study converged — so an
    unconverged FV52 reaches `run_benchmark` as `UNCONVERGED` rather than as a
    number nobody checked the discretisation error of.
    """
    solver = ModalEigenSolver()
    case = fv52_case()
    meshes: dict[float, TetMesh] = {}

    def sample(element_size_mm: float) -> tuple[TetMesh, float]:
        divisions = int(round(FV52_SIDE_MM / element_size_mm))
        mesh = fv52_mesh(divisions)
        meshes[element_size_mm] = mesh
        return mesh, FV52_QUANTITY.read(mesh, solver.solve(mesh, case))

    sizes = tuple(FV52_SIDE_MM / n for n in FV52_DIVISIONS)
    study: ConvergenceStudy = run_study(
        FV52_QUANTITY.name, FV52_QUANTITY.unit, sizes, sample
    )

    finest_size = min(meshes)
    return BenchmarkRun(
        value=study.stated_value,
        convergence=study,
        provenance=RunProvenance(
            analysis="modal",
            quantity=FV52_QUANTITY.name,
            unit=FV52_QUANTITY.unit,
            value=study.stated_value,
            geometry_source=(
                f"structured box primitive built in memory: {FV52_SIDE_MM:g} x "
                f"{FV52_SIDE_MM:g} x {FV52_THICKNESS_MM:g} mm"
            ),
            mesh=meshes[finest_size],
            case=case,
            solver=identify_solver(solver),
            element_size_mm=finest_size,
            convergence=study,
            notes={"benchmark": "nafems-fv52"},
        ),
    )


# -- LE11: solid cylinder / taper / sphere under a temperature field ---------
#
# The one benchmark here whose load is a *field* rather than a number. Its
# temperature is prescribed as a formula of position, not solved for, which is
# why it needed `LinearStaticSolver`'s `temperatures=` argument and not the
# conduction solver: those are two different things and E7 task 6 named only
# the second for a while.
#
# The geometry lives in `app/verify/le11_geometry.py` and its sourcing record is
# `docs/nafems-le11-geometry.md` — the manuals that reproduce the target do not
# print the shape, and it was recovered from a reprinted NAFEMS figure plus two
# solver inputs that agree with it.

LE11_TARGET_MPA: Final = -105.0

#: Point A, the lower inside corner, in mm.
LE11_POINT_A: Final = LE11_GEOMETRY_POINT_A

#: **The unit trap, and it is the most likely way to get this benchmark wrong.**
#: Every source publishes the field as `sqrt(x^2 + y^2) + z` with coordinates in
#: **metres**. This codebase is mm-N-MPa and converts nothing, so on a solid
#: built in mm the same physical field is that expression over 1000. Used
#: unchanged it gives temperatures — and therefore stresses — a thousand times
#: too large: about -105,000 MPa at A instead of -105, with nothing raising
#: anywhere. This is arithmetic on the cited formula, not a second source.
LE11_FIELD_SCALE: Final = 1000.0


def le11_temperatures(mesh: TetMesh) -> Any:
    """The prescribed temperature change at every node of a mesh, in kelvin.

    `Δθ = (sqrt(x^2 + y^2) + z) / 1000` for coordinates in mm — see
    `LE11_FIELD_SCALE`. It is a temperature *change* and the stress-free
    reference is zero, because every source writes it as `Δθ` applied to an
    unstressed body rather than as an absolute temperature.
    """
    import numpy as np

    nodes = np.asarray(mesh.nodes, dtype=np.float64)
    radius = np.hypot(nodes[:, 0], nodes[:, 1])
    return (radius + nodes[:, 2]) / LE11_FIELD_SCALE


LE11_MATERIAL: Final = Material(
    name="NAFEMS LE11 (E=210 GPa, nu=0.3, alpha=2.3e-4/K)",
    youngs_modulus_mpa=210_000.0,
    poissons_ratio=0.3,
    # Not part of the benchmark; `Material` requires it and this case never asks
    # for a factor of safety.
    yield_strength_mpa=250.0,
    density_kg_m3=7800.0,
    thermal_expansion_per_k=2.3e-4,
)

#: Signed, and the sign is the claim: the field heats the outside and the top
#: more than the bore, and A ends up in compression. A magnitude comparison
#: would pass a model whose field ran the other way.
LE11_QUANTITY: Final = stress_component_at(
    LE11_POINT_A, "zz", name="sigma_zz at point A on the base plane"
)

#: How far a level's meshed volume may fall short of the exact one. Same guard
#: and same reason as LE10's: a coarse mesh of a curved solid is a smaller body.
LE11_VOLUME_TOLERANCE: Final = 0.005

#: Chosen on the rule LE1 and LE10 use, stated before the sweep: every level
#: must resolve the geometry (), the spacing must be at
#: least 1.3 in *representative* size so the discretisation trend dominates
#: gmsh's remeshing noise, and take the finest triple that satisfies both.
#: Measured ratios 1.41 and 1.51.
#:
#: **This case needed the rule more than the others did.** Point A is a corner —
#: the inner sphere meeting the base plane — so the quantity is a point stress
#: in a steep gradient and it *scatters* with where nodes happen to land: over a
#: seven-size sweep the answer wandered between -105.3 and -107.1 MPa, every one
#: of them inside the +/-2% band and no three consecutive ones monotone. A
#: closer-spaced triple therefore measures the scatter rather than the mesh, and
#: the study correctly refuses to state a value from it.
LE11_ELEMENT_SIZES_MM: Final[tuple[float, ...]] = (270.0, 170.0, 105.0)


def le11_case() -> LoadCase:
    """The restraints exactly as the benchmark poses them, and no mechanical load.

    Four constraints and nothing else: the two cut planes of the quarter model
    are symmetry planes, and **both** the base annulus and the top annulus are
    held axially. The whole loading is the temperature field, which is why the
    single load below is a placeholder of zero magnitude — `LoadCase` requires
    at least one load and a thermal-only run genuinely has none.
    """
    band = 1.0
    return LoadCase(
        name="NAFEMS LE11 cylinder/taper/sphere under a temperature field",
        material=LE11_MATERIAL,
        fixtures=[
            Fixture(
                where=FaceSelector(axis="x", side="min", tolerance=0.001),
                kind="symmetry",
                normal="x",
                name="symmetry plane x = 0",
            ),
            Fixture(
                where=FaceSelector(axis="y", side="min", tolerance=0.001),
                kind="symmetry",
                normal="y",
                name="symmetry plane y = 0",
            ),
            # The base annulus, z = 0, between the two sphere radii.
            Fixture(
                where=BoxSelector(
                    min=(-1.0, -1.0, -band),
                    max=(
                        LE11_OUTER_SPHERE_RADIUS_MM + band,
                        LE11_OUTER_SPHERE_RADIUS_MM + band,
                        band,
                    ),
                ),
                dofs=["z"],
                name="base annulus (z = 0)",
            ),
            # The top annulus of the straight cylinder.
            Fixture(
                where=BoxSelector(
                    min=(-1.0, -1.0, LE11_TOTAL_HEIGHT_MM - band),
                    max=(
                        LE11_OUTER_CYLINDER_RADIUS_MM + band,
                        LE11_OUTER_CYLINDER_RADIUS_MM + band,
                        LE11_TOTAL_HEIGHT_MM + band,
                    ),
                ),
                dofs=["z"],
                name="top annulus",
            ),
        ],
        loads=[
            ForceLoad(
                where=FaceSelector(axis="z", side="max"),
                force_n=(0.0, 0.0, 0.0),
                name="no mechanical load; the loading is the temperature field",
            )
        ],
    )


def run_le11() -> BenchmarkRun:
    """Build LE11, mesh it at three sizes, apply the field, converge sigma_zz at A."""
    from app.manufacture.export import write_step
    from app.mesh.gmsh_mesher import generate_tet_mesh

    solver = LinearStaticSolver()
    case = le11_case()
    meshes: dict[float, TetMesh] = {}

    with tempfile.TemporaryDirectory(prefix="nafems-le11-") as workspace:
        step_path = Path(workspace) / "le11.step"
        write_step(le11_solid(), step_path)

        def sample(element_size_mm: float) -> tuple[TetMesh, float]:
            mesh, _ = generate_tet_mesh(
                step_path, "step", element_size_mm=element_size_mm, element_order=2
            )
            shortfall = abs(mesh.volume - LE11_VOLUME_MM3) / LE11_VOLUME_MM3
            if shortfall > LE11_VOLUME_TOLERANCE:
                raise MeshError(
                    f"At {element_size_mm:g} mm the mesh encloses {mesh.volume:.4g} mm^3 "
                    f"against the body's exact {LE11_VOLUME_MM3:.4g} mm^3 "
                    f"({shortfall * 100:.1f}% short): the straight element edges have "
                    "chorded the sphere and the taper so coarsely that this is a "
                    "smaller body, not a coarser mesh of the right one. Use a smaller "
                    "element_size_mm."
                )
            meshes[element_size_mm] = mesh
            output = solver.solve(mesh, case, temperatures=le11_temperatures(mesh))
            return mesh, LE11_QUANTITY.read(mesh, output)

        study = run_study(
            LE11_QUANTITY.name, LE11_QUANTITY.unit, LE11_ELEMENT_SIZES_MM, sample
        )

    if not meshes:  # pragma: no cover - every level would have to fail to mesh
        raise MeshError("No level of the LE11 study produced a mesh. " + study.report())

    finest = min(meshes)
    return BenchmarkRun(
        value=study.stated_value,
        convergence=study,
        provenance=RunProvenance(
            analysis="thermal-stress",
            quantity=LE11_QUANTITY.name,
            unit=LE11_QUANTITY.unit,
            value=study.stated_value,
            geometry_source=(
                "quarter cylinder/taper/sphere built with OCCT and meshed from STEP; "
                "see app/verify/le11_geometry.py and docs/nafems-le11-geometry.md"
            ),
            mesh=meshes[finest],
            case=case,
            solver=identify_solver(solver),
            element_size_mm=finest,
            convergence=study,
            notes={"benchmark": "nafems-le11"},
        ),
    )


# -- the elliptical annulus LE1 and LE10 share -------------------------------
#
# One quarter of an elliptical annulus, defined once because two benchmarks use
# it and a second copy of four numbers is a second chance to get one wrong.
# Sourced twice over and independently: the Abaqus LE10 entry states them for
# the thick plate, and FeenoX's LE1 Gmsh input states the same four for the
# membrane.

ANNULUS_OUTER_A: Final = 3250.0
ANNULUS_OUTER_B: Final = 2750.0
ANNULUS_INNER_A: Final = 2000.0
ANNULUS_INNER_B: Final = 1000.0

#: Area of the quarter annulus in mm^2, from the two ellipse areas. Both cases
#: use it as the guard that catches a mesh which has chorded the curved
#: boundary so coarsely that it is a *smaller region*, not a coarser mesh of
#: the right one.
ANNULUS_AREA_MM2: Final = (
    math.pi / 4.0 * (ANNULUS_OUTER_A * ANNULUS_OUTER_B - ANNULUS_INNER_A * ANNULUS_INNER_B)
)


# -- LE1: elliptic membrane under a uniformly distributed load ---------------
#
# **LE1 and LE10 are the same plan geometry.** The quarter elliptical annulus is
# identical — outer 3250 x 2750, inner 2000 x 1000, point D at (2000, 0) — and
# the two benchmarks differ in what they do with it: LE10 is 600 mm thick,
# pressed on its face and solved as a solid; LE1 is a 100 mm membrane pulled
# outward on its curved edge and solved in plane stress. They are therefore
# defined from one set of constants, so the two cases can never disagree about
# the shape they share.
#
# That identity is also what closed the document blocker. LE1 was catalogued as
# blocked partly on geometry the reproducing manuals do not print, and the
# lesson is worth keeping: three vendor manuals all quote the target and none
# quote the ellipses, while an open-source verification suite ships the Gmsh
# input that states them, and the numbers it states are the ones the
# independently-sourced LE10 entry already carried.

LE1_THICKNESS_MM: Final = 100.0
LE1_PRESSURE_MPA: Final = 10.0

#: Point D: the inner edge on the plane y = 0. A free edge, so the quantity is
#: the tangential edge stress there, which is what makes LE1 a test of how well
#: an element represents a curved free boundary rather than of anything in the
#: interior.
LE1_POINT_D: Final = (ANNULUS_INNER_A, 0.0, 0.0)

#: How far a level's meshed area may fall short of the exact one before the
#: level is refused. Same guard, same reason and the same tightness as LE10's
#: volume check: a coarse mesh of a curved boundary is a smaller region, not a
#: coarser mesh of the right one, and Richardson extrapolation over a sequence
#: of different bodies produces a number with no meaning.
LE1_AREA_TOLERANCE: Final = 0.005

LE1_MATERIAL: Final = Material(
    name="NAFEMS LE1 membrane (E=210 GPa, nu=0.3)",
    youngs_modulus_mpa=210_000.0,
    poissons_ratio=0.3,
    # Not part of the benchmark. `Material` requires it and a linear static run
    # reads it only to report a factor of safety, which this case never asks for.
    yield_strength_mpa=250.0,
    density_kg_m3=7800.0,
)

#: Tensile, and the sign is the claim: the membrane is pulled outward, so the
#: tangential stress at the inner edge is positive. A magnitude would pass a
#: model loaded the wrong way round.
LE1_QUANTITY: Final = stress_component_at(
    LE1_POINT_D, "yy", name="sigma_yy at point D on the inner free edge"
)

#: How far off the outer ellipse a node may sit and still be part of the loaded
#: edge, as a fraction of the normalised radius. Not arbitrary: gmsh puts corner
#: nodes exactly on the curve, but `Mesh.SecondOrderLinear` places each midside
#: node at the straight midpoint of its chord, which is *inside* the ellipse by
#: the sagitta h^2/(8R). At the coarsest level used here — 74 mm on a 2750 mm
#: semi-axis — that is 0.25 mm, or 9.1e-5 normalised, so a band below about 1e-4
#: would silently drop every midside node on the loaded edge and apply the
#: traction through the corners alone, which for a quadratic edge is not even
#: statically equivalent. 1e-2 clears that by two orders of magnitude and is
#: still two orders inside the first ring of interior nodes, which sit ~h/R
#: away — 2.7e-2 at the coarsest level.
#:
#: The band is therefore loose relative to what these three grids need, and
#: deliberately so: it has to hold for any element size a future level might
#: use, and the quantity it must stay below (the interior spacing) shrinks with
#: refinement while the quantity it must stay above (the sagitta) shrinks
#: faster. Note the consequence for testing — a break of this constant only
#: bites below ~1e-4, so a "tighten it a bit" break passes and proves nothing.
LE1_EDGE_TOLERANCE: Final = 0.01

#: Three levels for the Grid Convergence Index, chosen on two rules stated
#: before the sweep rather than picked from the best-looking triple afterwards:
#: every level must resolve the geometry (`LE1_AREA_TOLERANCE`), and the spacing
#: must be wide enough in *representative* size that the discretisation trend
#: dominates the noise of gmsh remeshing rather than refining. Unlike LE10 the
#: area guard is never close to binding here — a plane mesh puts its boundary
#: nodes exactly on the curve and only chords between them, so even the coarsest
#: level below is 0.09% short where LE10's coarsest solid was 7% — but the guard
#: stays, because "it did not bind this time" is not a reason to remove a check.
LE1_ELEMENT_SIZES_MM: Final[tuple[float, ...]] = (74.0, 55.0, 40.0)

#: The formal order of a quadratic triangle, passed to the study so it can say
#: for itself when the grids disagree with theory. They do here — the observed
#: order comes out near 4.7 — and that is worth publishing rather than hiding:
#: an order well above the formal one is the standard signal that a sequence is
#: not yet strictly in the asymptotic range, so the GCI understates the true
#: discretisation error. It does: Richardson extrapolates these three grids to
#: about 92.46 MPa where the reference is 92.7, a gap five times the 0.05% GCI.
#: The case still validates on the measured value against the published one,
#: which is what the target is for; the caution rides along so nobody reads a
#: 0.05% GCI as a claim that the answer is good to 0.05%.
LE1_FORMAL_ORDER: Final = 2.0


def le1_face() -> Any:
    """The quarter annulus as a planar OCCT face in z = 0.

    A face, not a thin solid, and that is the whole point of the case: LE1 is
    posed in plane stress, and re-posing it as a solid one element thick would
    be answering a different question with the same picture.
    """
    from app.kernel.occt.binding import require, symbol

    require()

    def ellipse_face(major: float, minor: float) -> Any:
        axes = symbol("gp_Ax2")(
            symbol("gp_Pnt")(0.0, 0.0, 0.0),
            symbol("gp_Dir")(0.0, 0.0, 1.0),
            symbol("gp_Dir")(1.0, 0.0, 0.0),
        )
        wire = symbol("BRepBuilderAPI_MakeWire")()
        wire.Add(
            symbol("BRepBuilderAPI_MakeEdge")(symbol("gp_Elips")(axes, major, minor)).Edge()
        )
        return symbol("BRepBuilderAPI_MakeFace")(wire.Wire()).Face()

    annulus = symbol("BRepAlgoAPI_Cut")(
        ellipse_face(ANNULUS_OUTER_A, ANNULUS_OUTER_B),
        ellipse_face(ANNULUS_INNER_A, ANNULUS_INNER_B),
    ).Shape()
    # The first quadrant, taken with a box that is deep enough in z to contain
    # the face on both sides of its own plane — a box that merely touches z = 0
    # intersects a coplanar face on a tolerance rather than on geometry.
    quadrant = symbol("BRepPrimAPI_MakeBox")(
        symbol("gp_Pnt")(0.0, 0.0, -1.0),
        2.0 * ANNULUS_OUTER_A,
        2.0 * ANNULUS_OUTER_B,
        2.0,
    ).Shape()
    return symbol("BRepAlgoAPI_Common")(annulus, quadrant).Shape()


# -- LE10: thick plate under pressure ----------------------------------------
#
# A quarter of an elliptical annular plate, 600 mm thick, pressed with 1 MPa on
# its upper surface and simply supported around its outer edge. Solid elements,
# so the only thing between this codebase and the answer was the shape — and
# then two things nobody had noticed until the shape existed. Both are recorded
# where they were fixed rather than here: `SolveOutput.nodal_stress` (a stress at
# a point is a nodal question and reading it at an element centroid is a 25%
# under-read on a plate in bending) and `EllipticalWallSelector` (no member of
# the region vocabulary could name a curved outer edge).

LE10_THICKNESS_MM: Final = 600.0
LE10_PRESSURE_MPA: Final = 1.0

#: Point D: the inner edge on the plane y = 0, on the pressed surface. The
#: benchmark's target is the direct stress *there*, and it is a corner of the
#: quarter model — the inner elliptical wall meets the symmetry plane meets the
#: top face — so a mesher puts a node exactly on it and the reader finds it
#: without interpolating.
LE10_POINT_D: Final = (ANNULUS_INNER_A, 0.0, LE10_THICKNESS_MM)

#: Exact volume of the quarter annular plate, from the ellipse areas. Not a
#: convenience: it is the guard that catches a level whose mesh has chorded the
#: curved boundary so coarsely that it is solving a *smaller part*. That
#: happened at 300 mm — 7% of the material gone, the answer moved with it, and
#: nothing else in the study could have told the difference between a
#: discretisation error and a different body.
LE10_VOLUME_MM3: Final = (
    math.pi
    / 4.0
    * (ANNULUS_OUTER_A * ANNULUS_OUTER_B - ANNULUS_INNER_A * ANNULUS_INNER_B)
    * LE10_THICKNESS_MM
)

#: How far a level's meshed volume may fall short of the exact one. Tight,
#: because this is not measuring the answer — it is asking whether the mesh is
#: of the right solid at all.
LE10_VOLUME_TOLERANCE: Final = 0.005

#: Target element sizes, in mm. Chosen on two rules stated before the case was
#: first run, both of which are about the *sequence* rather than about the
#: answer:
#:
#: 1. **Every level must resolve the geometry** — see `LE10_VOLUME_MM3`.
#: 2. **The levels must be far enough apart that the discretisation trend
#:    dominates the remeshing noise.** gmsh builds a fresh unstructured mesh at
#:    each size rather than refining the previous one, so consecutive levels are
#:    not nested and the quantity wanders by a few tenths of a percent for
#:    reasons that are not convergence. Neighbouring sizes let that noise
#:    dominate the signal and the study correctly refuses them; a spacing near
#:    1.4 in representative size does not.
LE10_ELEMENT_SIZES_MM: Final[tuple[float, ...]] = (245.0, 165.0, 115.0)

LE10_MATERIAL: Final = Material(
    name="NAFEMS LE10 plate (E=210 GPa, nu=0.3)",
    youngs_modulus_mpa=210_000.0,
    poissons_ratio=0.3,
    # Not part of the benchmark. `Material` requires it and a linear static run
    # reads it only to report a factor of safety, which this case never asks for.
    yield_strength_mpa=250.0,
    density_kg_m3=7800.0,
)

#: Signed, and the sign is the claim. The plate sags under the pressure, so the
#: pressed surface is in compression at D; the same benchmark is quoted as
#: +5.38 MPa by manuals that read it on the opposite face. Von Mises could not
#: express either.
LE10_QUANTITY: Final = stress_component_at(
    LE10_POINT_D, "yy", name="sigma_yy at point D on the pressed surface"
)


def _le10_half(z0: float, height: float) -> Any:
    """One half-thickness of the quarter plate, as an OCCT solid."""
    from app.kernel.occt.binding import symbol

    def prism(major: float, minor: float) -> Any:
        axes = symbol("gp_Ax2")(
            symbol("gp_Pnt")(0.0, 0.0, z0),
            symbol("gp_Dir")(0.0, 0.0, 1.0),
            symbol("gp_Dir")(1.0, 0.0, 0.0),
        )
        wire = symbol("BRepBuilderAPI_MakeWire")()
        wire.Add(
            symbol("BRepBuilderAPI_MakeEdge")(
                symbol("gp_Elips")(axes, major, minor)
            ).Edge()
        )
        face = symbol("BRepBuilderAPI_MakeFace")(wire.Wire()).Face()
        return symbol("BRepPrimAPI_MakePrism")(
            face, symbol("gp_Vec")(0.0, 0.0, height)
        ).Shape()

    annulus = symbol("BRepAlgoAPI_Cut")(
        prism(ANNULUS_OUTER_A, ANNULUS_OUTER_B), prism(ANNULUS_INNER_A, ANNULUS_INNER_B)
    ).Shape()
    quadrant = symbol("BRepPrimAPI_MakeBox")(
        symbol("gp_Pnt")(0.0, 0.0, z0 - 1.0),
        2.0 * ANNULUS_OUTER_A,
        2.0 * ANNULUS_OUTER_B,
        height + 2.0,
    ).Shape()
    return symbol("BRepAlgoAPI_Common")(annulus, quadrant).Shape()


def le10_solid() -> Any:
    """The quarter plate, **built as two half-thicknesses fused together**.

    The seam is the whole reason. LE10 restrains `uz` along line EE' — the
    midplane of the outer edge — and on a solid extruded in one piece there is
    no edge there, so a mesher puts nodes near that height only by accident. The
    restraint then landed on 2 nodes at one grid size and 38 at the next, and the
    answer scattered between -3.2 and -6.1 MPa with nothing in the study able to
    say why. Fusing two halves leaves a real edge at mid-thickness, the mesher
    puts a node ring on it, and the support is the same support at every
    refinement — which is what a convergence study assumes and cannot check.
    """
    from app.kernel.occt.binding import require, symbol

    require()
    half = LE10_THICKNESS_MM / 2.0
    return symbol("BRepAlgoAPI_Fuse")(_le10_half(0.0, half), _le10_half(half, half)).Shape()


def le1_case() -> PlaneCase:
    """The membrane exactly as the benchmark poses it.

    Two symmetry restraints and one edge traction. The sign of the traction is
    the whole model: the benchmark says a uniform **outward** pressure, and
    `PressureLoad` is positive inward, so this is negative ten and not positive
    ten. Loaded the wrong way the membrane is squeezed instead of stretched, the
    stress at D comes back at very nearly the right magnitude with the wrong
    sign, and only a signed target catches it.
    """
    return PlaneCase(
        name="NAFEMS LE1 elliptic membrane",
        material=LE1_MATERIAL,
        thickness_mm=LE1_THICKNESS_MM,
        state=PlaneState.STRESS,
        fixtures=[
            # Edge AB, the line x = 0: a symmetry plane of the full membrane, so
            # only ux is held. The tolerance is tightened from the default for
            # the same reason as LE10's — 1% of a 3250 mm bounding box is a
            # 32 mm band, which on the coarse level is a third of an element.
            Fixture(
                where=FaceSelector(axis="x", side="min", tolerance=0.001),
                dofs=["x"],
                kind="symmetry",
                normal="x",
                name="edge AB (x = 0)",
            ),
            # Edge CD, the line y = 0.
            Fixture(
                where=FaceSelector(axis="y", side="min", tolerance=0.001),
                dofs=["y"],
                kind="symmetry",
                normal="y",
                name="edge CD (y = 0)",
            ),
        ],
        loads=[
            PressureLoad(
                where=EllipticalWallSelector(
                    axis="z",
                    axis_point=(0.0, 0.0, 0.0),
                    semi_axis_a=ANNULUS_OUTER_A,
                    semi_axis_b=ANNULUS_OUTER_B,
                    tolerance=LE1_EDGE_TOLERANCE,
                ),
                pressure_mpa=-LE1_PRESSURE_MPA,
                name="edge BC (10 MPa outward)",
            ),
        ],
    )


def run_le1() -> BenchmarkRun:
    """Build LE1, mesh it at three sizes, solve in plane stress, converge at D.

    The face is written to STEP once and meshed from that file at each level, so
    every grid is a discretisation of the same region — building it per level
    would put the boolean operations inside the loop and make a geometry change
    indistinguishable from a mesh change.
    """
    from app.manufacture.export import write_step
    from app.mesh.gmsh_mesher import generate_tri_mesh

    solver = PlaneSolver()
    case = le1_case()
    meshes: dict[float, TriMesh] = {}

    with tempfile.TemporaryDirectory(prefix="nafems-le1-") as workspace:
        step_path = Path(workspace) / "le1.step"
        write_step(le1_face(), step_path)

        def sample(element_size_mm: float) -> tuple[TriMesh, float]:
            mesh, _ = generate_tri_mesh(
                step_path, "step", element_size_mm=element_size_mm, element_order=2
            )
            shortfall = abs(mesh.area - ANNULUS_AREA_MM2) / ANNULUS_AREA_MM2
            if shortfall > LE1_AREA_TOLERANCE:
                raise MeshError(
                    f"At {element_size_mm:g} mm the mesh covers {mesh.area:.4g} mm^2 "
                    f"against the membrane's exact {ANNULUS_AREA_MM2:.4g} mm^2 "
                    f"({shortfall * 100:.1f}% short): the straight element edges have "
                    "chorded the elliptical boundary so coarsely that this is a smaller "
                    "region, not a coarser mesh of the right one. Use a smaller "
                    "element_size_mm."
                )
            meshes[element_size_mm] = mesh
            return mesh, LE1_QUANTITY.read(mesh, solver.solve(mesh, case))

        study = run_study(
            LE1_QUANTITY.name,
            LE1_QUANTITY.unit,
            LE1_ELEMENT_SIZES_MM,
            sample,
            formal_order=LE1_FORMAL_ORDER,
        )

    if not meshes:  # pragma: no cover - every level would have to fail to mesh
        raise MeshError("No level of the LE1 study produced a mesh. " + study.report())

    finest = min(meshes)
    return BenchmarkRun(
        value=study.stated_value,
        convergence=study,
        provenance=RunProvenance(
            analysis="linear-static",
            quantity=LE1_QUANTITY.name,
            unit=LE1_QUANTITY.unit,
            value=study.stated_value,
            geometry_source=(
                "quarter elliptical annular membrane built with OCCT as a planar face "
                f"and meshed from STEP: outer {ANNULUS_OUTER_A:g} x {ANNULUS_OUTER_B:g} "
                f"mm, inner {ANNULUS_INNER_A:g} x {ANNULUS_INNER_B:g} mm, "
                f"{LE1_THICKNESS_MM:g} mm thick, plane stress"
            ),
            mesh=meshes[finest],
            case=case,
            solver=identify_solver(solver),
            element_size_mm=finest,
            convergence=study,
            notes={"benchmark": "nafems-le1"},
        ),
    )


def le10_case() -> LoadCase:
    """The load case exactly as the benchmark poses it.

    Four restraints and one pressure, and every one of them is a *region* named
    geometrically rather than a list of node numbers — which is why the same case
    is solvable on all three meshes.
    """
    band = 1.0

    def outer_wall(z0: float, length: float | None) -> EllipticalWallSelector:
        return EllipticalWallSelector(
            axis="z",
            axis_point=(0.0, 0.0, z0),
            semi_axis_a=ANNULUS_OUTER_A,
            semi_axis_b=ANNULUS_OUTER_B,
            tolerance=0.01,
            length=length,
        )

    return LoadCase(
        name="NAFEMS LE10 thick plate under pressure",
        material=LE10_MATERIAL,
        fixtures=[
            # Face DCD'C', the plane y = 0: a symmetry plane of the full plate.
            # The tolerance is tightened from the default because the default is
            # a fraction of the bounding box, and 1% of 2750 mm is a 27 mm slab
            # of material held flat rather than a face.
            Fixture(
                where=FaceSelector(axis="y", side="min", tolerance=0.001),
                kind="symmetry",
                normal="y",
                name="face DCD'C' (y = 0)",
            ),
            # Face ABA'B', the plane x = 0.
            Fixture(
                where=FaceSelector(axis="x", side="min", tolerance=0.001),
                kind="symmetry",
                normal="x",
                name="face ABA'B' (x = 0)",
            ),
            # Face BCB'C', the outer elliptical wall: held in x and y over its
            # whole height, free to rotate — a simple support, not a clamp.
            Fixture(
                where=outer_wall(0.0, None),
                dofs=["x", "y"],
                name="face BCB'C' (outer wall)",
            ),
            # Line EE', the midplane of that wall: the only restraint on uz in
            # the whole model, which is what makes this simply supported rather
            # than built in.
            Fixture(
                where=outer_wall(LE10_THICKNESS_MM / 2.0 - band, 2.0 * band),
                dofs=["z"],
                name="line EE' (midplane of the outer wall)",
            ),
        ],
        loads=[
            PressureLoad(
                where=FaceSelector(axis="z", side="max"),
                pressure_mpa=LE10_PRESSURE_MPA,
                name="1 MPa on the upper surface",
            )
        ],
    )


def run_le10() -> BenchmarkRun:
    """Build LE10, mesh it at three sizes, solve, and converge sigma_yy at D.

    The solid is written to STEP once and meshed from that file at each level,
    so every grid is a discretisation of the same body — building it per level
    would put the boolean operations inside the loop and make a geometry change
    indistinguishable from a mesh change.
    """
    from app.manufacture.export import write_step
    from app.mesh.gmsh_mesher import generate_tet_mesh

    solver = LinearStaticSolver()
    case = le10_case()
    meshes: dict[float, TetMesh] = {}

    with tempfile.TemporaryDirectory(prefix="nafems-le10-") as workspace:
        step_path = Path(workspace) / "le10.step"
        write_step(le10_solid(), step_path)

        def sample(element_size_mm: float) -> tuple[TetMesh, float]:
            mesh, _ = generate_tet_mesh(
                step_path, "step", element_size_mm=element_size_mm, element_order=2
            )
            shortfall = abs(mesh.volume - LE10_VOLUME_MM3) / LE10_VOLUME_MM3
            if shortfall > LE10_VOLUME_TOLERANCE:
                raise MeshError(
                    f"At {element_size_mm:g} mm the mesh encloses {mesh.volume:.4g} mm^3 "
                    f"against the plate's exact {LE10_VOLUME_MM3:.4g} mm^3 "
                    f"({shortfall * 100:.1f}% short): the straight element edges have "
                    "chorded the elliptical boundary so coarsely that this is a smaller "
                    "part, not a coarser mesh of the right one. Use a smaller "
                    "element_size_mm."
                )
            meshes[element_size_mm] = mesh
            return mesh, LE10_QUANTITY.read(mesh, solver.solve(mesh, case))

        study = run_study(
            LE10_QUANTITY.name, LE10_QUANTITY.unit, LE10_ELEMENT_SIZES_MM, sample
        )

    if not meshes:  # pragma: no cover - every level would have to fail to mesh
        raise MeshError("No level of the LE10 study produced a mesh. " + study.report())

    finest = min(meshes)
    return BenchmarkRun(
        value=study.stated_value,
        convergence=study,
        provenance=RunProvenance(
            analysis="linear-static",
            quantity=LE10_QUANTITY.name,
            unit=LE10_QUANTITY.unit,
            value=study.stated_value,
            geometry_source=(
                "quarter elliptical annular plate built with OCCT and meshed from STEP: "
                f"outer {ANNULUS_OUTER_A:g} x {ANNULUS_OUTER_B:g} mm, inner {ANNULUS_INNER_A:g} x "
                f"{ANNULUS_INNER_B:g} mm, {LE10_THICKNESS_MM:g} mm thick"
            ),
            mesh=meshes[finest],
            case=case,
            solver=identify_solver(solver),
            element_size_mm=finest,
            convergence=study,
            notes={"benchmark": "nafems-le10"},
        ),
    )


# -- the catalogue -----------------------------------------------------------

CASES: Final[tuple[Case, ...]] = (
    Case(
        benchmark=Benchmark(
            id="nafems-le1",
            title="LE1 — elliptic membrane under a uniformly distributed load",
            analysis="linear-static",
            description=(
                "A quarter of an elliptical annulus in plane stress, pushed outward by "
                "10 MPa on its outer edge, with symmetry restraints on the two straight "
                "edges. The quantity is the tangential edge stress at D, on the inner "
                "boundary, where the answer is most sensitive to how well the element "
                "represents a curved free edge."
            ),
            target=Target(
                basis=TargetBasis.PUBLISHED,
                unit="MPa",
                value=92.7,
                tolerance=0.02,
                tolerance_reason=(
                    "2% is the band the reproducing manual's own converged plane-stress "
                    "elements sit inside on this test. It is a statement about the "
                    "element, which is what LE1 exists to measure — a wider band would "
                    "pass an element the benchmark is designed to fail."
                ),
                source=SOURCES["abaqus-le1"],
            ),
            run=run_le1,
            slow=True,
            references=(
                SOURCES["abaqus-le1"],
                SOURCES["feenox-le1-geometry"],
                SOURCES["altair-le1"],
                "Geometry: the reproducing manuals quote LE1's target and none of them "
                "prints its ellipses — the Abaqus entry says the curves are 'given "
                "above' beside a figure that is not in the text. The semi-axes come "
                "from FeenoX's committed Gmsh input, and they are the same four the "
                "independently-sourced LE10 entry already carried: LE1 and LE10 are one "
                "plan geometry, a 100 mm membrane and a 600 mm plate.",
                "Convergence: the observed order is about 4.7 against a formal order of "
                "2 for a quadratic triangle, so these grids are not strictly in the "
                "asymptotic range and the 0.05% GCI understates the discretisation "
                "error — Richardson extrapolates them to ~92.46 MPa where the reference "
                "is 92.7. The case validates on the measured value against the "
                "published one, which is what the target is for; the discrepancy is "
                "published rather than smoothed away.",
            ),
        ),
    ),
    Case(
        benchmark=Benchmark(
            id="nafems-le3",
            title="LE3 — hemisphere under point loads",
            analysis="linear-static",
            description=(
                "A hemispherical shell pulled outward by 2 kN at A and pushed inward by "
                "2 kN at C, on two symmetry edges, held at the pole. The classic test of "
                "whether a shell element can represent inextensional bending: almost all "
                "the 185 mm of movement is the shell changing shape rather than "
                "stretching, so an element that locks reports a small fraction of it."
            ),
            target=Target(
                basis=TargetBasis.PUBLISHED,
                unit="mm",
                value=185.0,
                tolerance=0.02,
                tolerance_reason=(
                    "The failure this test is built to expose — membrane locking — is an "
                    "order-of-magnitude error, not a few percent, so any band in this "
                    "region separates a working element from a locking one. 2% keeps it "
                    "a statement about accuracy rather than about survival."
                ),
                source=SOURCES["abaqus-le3"],
            ),
            blocked_reason=blocked(
                Blocker.NO_SHELL_SOLVER,
                "LE3 is a shell benchmark and its whole subject is shell bending under "
                "point loads.",
            ),
            references=(SOURCES["abaqus-le3"],),
        ),
        blocker=Blocker.NO_SHELL_SOLVER,
    ),
    Case(
        benchmark=Benchmark(
            id="nafems-le10",
            title="LE10 — thick plate under pressure",
            analysis="linear-static",
            description=(
                "The LE1 ellipse thickened to 0.6 m and loaded by 1 MPa of uniform "
                "pressure on its upper surface, restrained by symmetry on two faces, in "
                "x and y on a third, and out of plane only along the midplane line EE'. "
                "Solid elements, so the only thing between this codebase and the answer "
                "is the shape itself."
            ),
            target=Target(
                basis=TargetBasis.PUBLISHED,
                unit="MPa",
                value=-5.38,
                tolerance=0.02,
                tolerance_reason=(
                    "A direct stress at a point on a curved surface, quoted to three "
                    "figures. 2% is the resolution the quoted figure supports; a looser "
                    "band would not distinguish a correct solid element from one that "
                    "has the pressure on the wrong face."
                ),
                source=SOURCES["abaqus-le10"],
            ),
            run=run_le10,
            slow=True,
            references=(
                SOURCES["abaqus-le10"],
                SOURCES["feenox-le10"],
                "Sign convention: the target is quoted here as the compressive stress on "
                "the *pressed* surface, which is the face point D sits on in this model. "
                "Manuals that place D on the opposite face quote the same result as "
                "+5.38 MPa — a plate in bending carries equal and opposite stress on its "
                "two faces, and the sign is which one is being read.",
            ),
        )
    ),
    Case(
        benchmark=Benchmark(
            id="nafems-le11",
            title="LE11 — solid cylinder/taper/sphere under a temperature field",
            analysis="thermal-stress",
            description=(
                "A solid of revolution — cylinder, taper, sphere — expanded by a "
                "temperature field that varies with position, restrained on three "
                "symmetry planes and on one face. The quantity is the axial direct "
                "stress at A."
            ),
            target=Target(
                basis=TargetBasis.PUBLISHED,
                unit="MPa",
                value=-105.0,
                tolerance=0.02,
                tolerance_reason=(
                    "The reproducing manual's own fine 20-node mesh reaches -103.26 MPa, "
                    "1.7% from the target, so a band tighter than 2% would mark a "
                    "correct, converged solid solution as deviating."
                ),
                source=SOURCES["abaqus-le11"],
            ),
            run=run_le11,
            slow=True,
            references=(
                SOURCES["abaqus-le11"],
                SOURCES["esrd-le11-geometry"],
                SOURCES["feenox-le11-geometry"],
                SOURCES["feenox-le11-model"],
                SOURCES["featool-le11-geometry"],
                SOURCES["altair-le11"],
                "Geometry: none of the manuals that reproduce this target print the "
                "shape. It was recovered from ESRD's reprint of the original NAFEMS "
                "dimensioned figure, cross-checked against two solver inputs; nine of "
                "ten dimensions agree exactly and the tenth was settled by arithmetic "
                "on the figure's own annotations. The full record, including what is "
                "inferred and from what, is docs/nafems-le11-geometry.md.",
                "Expect UNCONVERGED rather than VALIDATED, and that is the finding. "
                "Point A is a corner — the inner sphere meeting the base plane — so the "
                "quantity is a point stress in a steep gradient, and over a seven-size "
                "sweep the answer scattered between -105.3 and -107.1 MPa: every level "
                "inside the 2% band, and no three consecutive levels monotone. A "
                "tetrahedral mesh cannot state this corner stress to better than its "
                "own scatter, so the convergence study refuses to state a value at all. "
                "That refusal is the honest outcome under Decision 3 and is worth more "
                "than a number picked from the scatter: what it says is that this "
                "benchmark needs the curved-hex or p-version elements its published "
                "solutions use, which is a capability finding rather than a defect.",
            ),
        ),
    ),
    Case(
        benchmark=Benchmark(
            id="nafems-fv52",
            title='FV52 — simply supported "solid" square plate',
            analysis="modal",
            description=(
                "A 10 m square plate, 1 m thick, held out of plane along the four edges "
                "of its underside and free otherwise — so three rigid-body modes come "
                "first and the fundamental flexural mode is mode 4. Thick enough "
                "(h/a = 0.1) that thin-plate theory is 8% high, which is what makes it "
                "a solid-element benchmark rather than a shell one."
            ),
            target=Target(
                basis=TargetBasis.PUBLISHED,
                unit="Hz",
                value=44.092,
                tolerance=0.05,
                tolerance_reason=(
                    "5% covers discretisation on a structured Kuhn tet10 mesh at the "
                    "three grid levels an offline test suite can afford, against a "
                    "reference reproduced with incompatible-mode bricks. It is a band on "
                    "the *mesh*, not on the physics: a wrong restraint set moves this "
                    "frequency by tens of percent and is caught either way. Fixed before "
                    "the case was first run."
                ),
                source=SOURCES["abaqus-fv52"],
            ),
            run=run_fv52,
            slow=True,
            references=(
                SOURCES["abaqus-fv52"],
                "Recorded, not resolved: a second vendor manual (Altair OptiStruct "
                "OS-V: 0455) quotes 45.897 Hz for what it also calls mode 4 of FV52, and "
                "describes its restraint plane at z = -5 m, which cannot be the underside "
                "of a 1 m plate. The target above cites the manual whose full reference "
                "row was read verbatim and whose own C3D8I result reproduces every mode "
                "in it to 0.00%.",
            ),
        )
    ),
)

#: Every catalogued case, in the order above.
NAFEMS_SUITE: Final = Suite(
    name="nafems-standard-benchmarks",
    benchmarks=tuple(case.benchmark for case in CASES),
)

BY_ID: Final[dict[str, Case]] = {case.benchmark.id: case for case in CASES}


def blockers() -> dict[Blocker, tuple[str, ...]]:
    """Which cases each missing thing is holding up, blocker to case ids.

    The catalogue's most useful output and the reason `Blocker` is an enum. A
    blocker holding three cases is worth building; one holding a single case may
    not be, and free-text reasons cannot be counted.
    """
    grouped: dict[Blocker, list[str]] = {}
    for case in CASES:
        if case.blocker is None:
            continue
        grouped.setdefault(case.blocker, []).append(case.benchmark.id)
    return {blocker: tuple(ids) for blocker, ids in grouped.items()}


def report() -> str:
    """A paragraph a person can act on: what runs, what does not, and why."""
    runnable = [case for case in CASES if case.benchmark.runnable]
    lines = [
        f"{NAFEMS_SUITE.name}: {len(runnable)} of {len(CASES)} cases run here.",
    ]
    for blocker, ids in sorted(blockers().items(), key=lambda item: str(item[0])):
        lines.append(f"  {blocker} blocks {len(ids)}: {', '.join(ids)}")
        lines.append(f"    {BLOCKER_DETAIL[blocker]}")
    return "\n".join(lines)


__all__ = [
    "BLOCKER_DETAIL",
    "BY_ID",
    "CASES",
    "FV52_DIVISIONS",
    "FV52_MATERIAL",
    "FV52_QUANTITY",
    "FV52_SIDE_MM",
    "FV52_THICKNESS_MM",
    "NAFEMS_SUITE",
    "SOURCES",
    "Blocker",
    "Case",
    "blocked",
    "blockers",
    "fv52_case",
    "fv52_fixtures",
    "fv52_mesh",
    "report",
    "run_fv52",
]
