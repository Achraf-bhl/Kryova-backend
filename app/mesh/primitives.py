"""Analytically exact meshes, for verification and tests.

A structured box mesh has a known volume and known closed-form behaviour under
simple load cases, which is what makes it possible to check the solver against
hand calculations rather than against its own previous output.
"""

import numpy as np

from app.mesh.types import TET10_EDGES, TetMesh

# Kuhn decomposition: the six tets that tile a unit cube without adding nodes.
_CUBE_TETS = np.array(
    [
        [0, 1, 3, 7],
        [0, 1, 7, 5],
        [0, 5, 7, 4],
        [0, 3, 2, 7],
        [0, 6, 4, 7],
        [0, 2, 6, 7],
    ],
    dtype=np.int64,
)


def box_mesh(
    size: tuple[float, float, float], divisions: tuple[int, int, int] = (4, 4, 4)
) -> TetMesh:
    """A structured tet mesh of an axis-aligned box with a corner at the origin.

    `size` is in mm; `divisions` is the number of cells along each axis.
    """
    if any(s <= 0 for s in size):
        raise ValueError("box dimensions must be positive")
    if any(d < 1 for d in divisions):
        raise ValueError("each axis needs at least one division")

    nx, ny, nz = divisions
    xs = np.linspace(0.0, size[0], nx + 1)
    ys = np.linspace(0.0, size[1], ny + 1)
    zs = np.linspace(0.0, size[2], nz + 1)

    grid = np.stack(np.meshgrid(xs, ys, zs, indexing="ij"), axis=-1)
    nodes = grid.reshape(-1, 3)

    def node_id(i, j, k):
        return (i * (ny + 1) + j) * (nz + 1) + k

    i, j, k = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    corners = np.stack(
        [node_id(i + di, j + dj, k + dk) for di in (0, 1) for dj in (0, 1) for dk in (0, 1)],
        axis=-1,
    ).reshape(-1, 8)

    tets = corners[:, _CUBE_TETS].reshape(-1, 4)

    mesh = TetMesh(nodes=nodes, tets=tets)
    # Kuhn tets can come out inverted depending on corner ordering; fix winding
    # so downstream code can rely on positive volumes.
    negative = mesh.signed_volumes() < 0
    if negative.any():
        mesh.tets[negative] = mesh.tets[negative][:, [0, 2, 1, 3]]
        mesh = TetMesh(nodes=mesh.nodes, tets=mesh.tets)
    return mesh


def promote_to_tet10(mesh: TetMesh) -> TetMesh:
    """Add a midside node at the middle of every edge, giving a tet10 mesh.

    Edges stay straight, so the element geometry -- and therefore the volume and
    the boundary surface -- is exactly the tet4 mesh's. Only the interpolation
    order changes, which is what makes a tet4-versus-tet10 comparison at equal
    element count a comparison of the elements rather than of two meshes.
    """
    if mesh.midside is not None:
        return mesh

    pairs = mesh.tets[:, np.asarray(TET10_EDGES, dtype=np.int64)]  # (n_tets, 6, 2)
    # Sorting each pair makes the two elements sharing an edge agree on its key,
    # so the shared edge gets one node rather than two coincident ones.
    keys = np.sort(pairs, axis=2).reshape(-1, 2)
    unique_edges, inverse = np.unique(keys, axis=0, return_inverse=True)

    midpoints = 0.5 * (mesh.nodes[unique_edges[:, 0]] + mesh.nodes[unique_edges[:, 1]])
    return TetMesh(
        nodes=np.vstack([mesh.nodes, midpoints]),
        tets=mesh.tets.copy(),
        midside=(len(mesh.nodes) + inverse).reshape(len(mesh.tets), 6),
    )
