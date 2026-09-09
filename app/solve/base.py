from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.types import LoadCase, ModalCase, ModalResult, StaticResult

if TYPE_CHECKING:
    # `ThermalCase` and `ThermalField` are defined in `app.solve.conduction`,
    # which imports `app.solve.linear_static`, which imports this module — so a
    # runtime import here would be a cycle. Under `TYPE_CHECKING` it is not, and
    # the two annotations below are quoted accordingly.
    #
    # **The alternative was to put the ABC beside its concrete class**, which is
    # what `app.solve.plane.PlanarSolver` does and is why that one is not here.
    # It is stated as a cost rather than hidden: this file is the register of
    # analyses this codebase treats as separable, and a reader asking "what can
    # be swapped out independently?" should find the answer in one place instead
    # of grepping for `ABC` across the package. One `TYPE_CHECKING` import is
    # the price of that, and it buys the fourth sibling being visible next to
    # the three it is a sibling of.
    from app.solve.conduction import ThermalCase, ThermalField


@dataclass
class SolveOutput:
    """Full solver output. `result` is the summary that gets persisted; the
    arrays are large and belong in object storage, not the database.

    `nodal_stress` is **optional, and that is the decision rather than an
    oversight.** Both solvers here produce it — CalculiX writes it into the
    `.frd` and the in-house solver averages its element stress onto the nodes —
    but the `Solver` seam exists so a surrogate can drop in, and one that
    predicts displacement and a von Mises field without a full tensor is a
    plausible surrogate. Requiring six components would exclude it for the sake
    of a field most callers never read. A caller that needs the tensor asks for
    it and is refused **by name** when it is absent, the same shape
    `SolverIdentity.version` uses for a solver that declares no version.
    """

    result: StaticResult
    displacements: NDArray[np.float64] = field(repr=False)  # (n_nodes, 3), mm
    von_mises: NDArray[np.float64] = field(repr=False)  # (n_elements,), MPa
    #: (n_nodes, 6) in MPa, ordered SXX SYY SZZ SXY SYZ SZX — CalculiX's own
    #: order, adopted here so the adapter does not permute on every read. Nodal
    #: rather than per-element because the quantity it exists for is a stress
    #: *at a point*, usually on a surface, and an element value sits half an
    #: element inside the material: on a plate four elements thick that is a
    #: 25% under-read of a bending stress, which looks like a converging answer
    #: rather than like an error.
    nodal_stress: NDArray[np.float64] | None = field(default=None, repr=False)


class Solver(ABC):
    """Interface every stress solver implements.

    Kept deliberately narrow — mesh in, load case in, fields out — so a faster
    or surrogate solver can be swapped in without the API, job layer, or AI
    layer knowing which one ran.
    """

    name: str

    @abstractmethod
    def solve(self, mesh: TetMesh, case: LoadCase) -> SolveOutput: ...


@dataclass
class ModalOutput:
    """Full modal output. `result` is persisted; the shapes are not.

    `shapes` is (n_modes, n_nodes, 3) -- one displacement field per mode, each
    mass-normalised. They are as large as a static displacement field per mode,
    so they belong in object storage with the rest of the arrays.
    """

    result: ModalResult
    frequencies_hz: NDArray[np.float64] = field(repr=False)  # (n_modes,)
    shapes: NDArray[np.float64] = field(repr=False)  # (n_modes, n_nodes, 3)


class ModalSolver(ABC):
    """Interface every natural-frequency solver implements.

    A sibling of `Solver`, not a method on it, for the same reason `Solver` is
    narrow: the two answer different questions from different inputs, and a
    single `solve` taking a union of cases would make every caller branch on
    what it got back. A surrogate that predicts frequencies can drop in here
    without the static path knowing it exists.
    """

    name: str

    @abstractmethod
    def solve(self, mesh: TetMesh, case: ModalCase) -> ModalOutput: ...


class ConductionSolver(ABC):
    """Interface every steady-state heat-conduction solver implements.

    Mesh in, `ThermalCase` in, `ThermalField` out.

    **The fourth sibling, and a sibling for the same reason as the other
    three.** A conduction run takes a `ThermalCase` — a conductivity, fixed
    temperatures, convection films, heat fluxes, a volumetric source — and none
    of that is expressible as a `LoadCase`: there are no fixtures and no forces,
    and the material property it needs is the one property `Material` does not
    carry. Folding it into `Solver.solve` would make that method take a union of
    cases and return a union of outputs, and **every** caller would then have to
    re-derive which of the two it was holding — the job runner, the verification
    harness, an oracle comparison. Keeping them apart is what lets CalculiX's
    `*HEAT TRANSFER` step be federated behind this seam later without the static
    path knowing it exists, exactly as `CalculiXSolver` sits behind `Solver`.

    **The output type is its own, and that is the decision.** `PlanarSolver`
    shares `SolveOutput` because a plane result genuinely *is* the same four
    fields — displacement, von Mises, a stress tensor, a summary — so a second
    type would have forked the viewer, the provenance record and the convergence
    harness for no gain. A temperature field is not any of those fields. It has
    one scalar per node in kelvin and a Fourier flux per element in W/m², and
    forcing it into `SolveOutput` would mean either a `displacements` array
    holding temperatures under a name that lies, or three more optional arrays
    that every static consumer must learn to ignore. `ThermalField` is the same
    shape of answer as `SolveOutput` — a small persisted summary plus large
    arrays that belong in object storage — without pretending to be the same
    quantity. That is the plane family's rule applied honestly in the other
    direction: share the type when the fields are the same, not when only the
    plumbing is.
    """

    name: str

    @abstractmethod
    def solve(self, mesh: TetMesh, case: "ThermalCase") -> "ThermalField": ...
