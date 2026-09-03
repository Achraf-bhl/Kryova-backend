from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.types import LoadCase, ModalCase, ModalResult, StaticResult


@dataclass
class SolveOutput:
    """Full solver output. `result` is the summary that gets persisted; the
    arrays are large and belong in object storage, not the database."""

    result: StaticResult
    displacements: NDArray[np.float64] = field(repr=False)  # (n_nodes, 3), mm
    von_mises: NDArray[np.float64] = field(repr=False)  # (n_elements,), MPa


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
