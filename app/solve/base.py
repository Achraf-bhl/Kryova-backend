from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.types import LoadCase, StaticResult


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
