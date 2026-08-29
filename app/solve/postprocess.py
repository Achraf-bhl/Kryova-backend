import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh


def nodal_average(mesh: TetMesh, element_values: NDArray[np.float64]) -> NDArray[np.float64]:
    """Spread constant-per-element values onto nodes by simple averaging.

    Element stress is discontinuous across element faces; averaging at the nodes
    is the standard smoothing that makes a contour plot readable. It is a
    display convenience only -- the reported peak stress stays the raw element
    value, so a stress concentration is never smoothed away from the headline
    number.

    Midside nodes are included for a tet10 mesh, so every node the viewer might
    address carries a value rather than a hole.
    """
    connectivity = mesh.connectivity
    totals = np.zeros(mesh.node_count, dtype=np.float64)
    counts = np.zeros(mesh.node_count, dtype=np.int64)
    np.add.at(totals, connectivity, element_values[:, None])
    np.add.at(counts, connectivity, 1)
    return np.divide(totals, counts, out=np.zeros_like(totals), where=counts > 0)
