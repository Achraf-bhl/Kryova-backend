import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh


def nodal_average(mesh: TetMesh, element_values: NDArray[np.float64]) -> NDArray[np.float64]:
    """Spread constant-per-element values onto nodes by simple averaging.

    Tet4 stress is discontinuous across element faces; averaging at the nodes is
    the standard smoothing that makes a contour plot readable. It is a display
    convenience only -- the reported peak stress stays the raw element value, so
    a stress concentration is never smoothed away from the headline number.
    """
    totals = np.zeros(mesh.node_count, dtype=np.float64)
    counts = np.zeros(mesh.node_count, dtype=np.int64)
    np.add.at(totals, mesh.tets, element_values[:, None])
    np.add.at(counts, mesh.tets, 1)
    return np.divide(totals, counts, out=np.zeros_like(totals), where=counts > 0)
