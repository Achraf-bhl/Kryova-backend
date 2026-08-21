"""Linear static structural FEA on 4-node tetrahedra.

Small-strain, isotropic, linear elastic. This is the ground-truth baseline the
PRD calls for: unremarkable, well-understood physics whose numbers can be
checked against closed-form solutions.

Tet4 elements are constant-strain, so they are noticeably stiff in bending.
Accuracy on bending-dominated parts comes from mesh refinement (or, later,
tet10 elements) -- not from anything clever here.
"""

import time
import warnings as warnings_module
from collections.abc import Sequence

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.base import SolveOutput, Solver
from app.solve.selection import distribute_force, select_nodes
from app.solve.types import LoadCase, Material, SolverError, StaticResult

# Derivatives of the tet4 shape functions with respect to natural coordinates.
_DN_DXI = np.array([[-1.0, -1.0, -1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])

_MIN_JACOBIAN = 1e-12

# Equilibrium residual accepted as "solved", relative to the applied load.
_RESIDUAL_TOLERANCE = 1e-8


def constitutive_matrix(material: Material) -> NDArray[np.float64]:
    """Isotropic elasticity matrix in Voigt form [xx, yy, zz, xy, yz, zx]."""
    e = material.youngs_modulus_mpa
    nu = material.poissons_ratio
    lam = e * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
    mu = e / (2.0 * (1.0 + nu))

    d = np.zeros((6, 6), dtype=np.float64)
    d[:3, :3] = lam
    d[0, 0] = d[1, 1] = d[2, 2] = lam + 2.0 * mu
    d[3, 3] = d[4, 4] = d[5, 5] = mu
    return d


def _shape_gradients(mesh: TetMesh) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Per-element dN/dx, shape (n_elem, 4, 3), and volume, shape (n_elem,)."""
    p = mesh.nodes[mesh.tets]
    jacobians = p[:, 1:] - p[:, :1]  # rows are the edge vectors leaving node 0
    dets = np.linalg.det(jacobians)

    degenerate = np.abs(dets) < _MIN_JACOBIAN
    if degenerate.any():
        raise SolverError(
            f"{int(degenerate.sum())} element(s) have zero volume; the mesh is degenerate"
        )

    # Chain rule through the natural coordinates gives dN/dx = dN/dxi @ inv(J).T
    inv_j = np.linalg.inv(jacobians)
    grads = _DN_DXI @ np.transpose(inv_j, (0, 2, 1))
    return grads, np.abs(dets) / 6.0


def _strain_displacement(grads: NDArray[np.float64]) -> NDArray[np.float64]:
    """B matrices, shape (n_elem, 6, 12), using engineering shear strains."""
    n = grads.shape[0]
    b = np.zeros((n, 6, 12), dtype=np.float64)
    gx, gy, gz = grads[..., 0], grads[..., 1], grads[..., 2]
    for node in range(4):
        col = 3 * node
        b[:, 0, col + 0] = gx[:, node]
        b[:, 1, col + 1] = gy[:, node]
        b[:, 2, col + 2] = gz[:, node]
        b[:, 3, col + 0] = gy[:, node]
        b[:, 3, col + 1] = gx[:, node]
        b[:, 4, col + 1] = gz[:, node]
        b[:, 4, col + 2] = gy[:, node]
        b[:, 5, col + 0] = gz[:, node]
        b[:, 5, col + 2] = gx[:, node]
    return b


def _element_dofs(tets: NDArray[np.int64]) -> NDArray[np.int64]:
    """Global DOF index for each element-local DOF, shape (n_elem, 12)."""
    return (tets[:, :, None] * 3 + np.arange(3)[None, None, :]).reshape(len(tets), 12)


_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _dof_indices(
    nodes: NDArray[np.int64], axes: Sequence[str] = ("x", "y", "z")
) -> NDArray[np.int64]:
    offsets = np.array([_AXIS_INDEX[axis] for axis in axes], dtype=np.int64)
    return (nodes[:, None] * 3 + offsets[None, :]).ravel()


def _residual_is_small(
    k_ff: sp.spmatrix, solution: NDArray[np.float64], applied: NDArray[np.float64]
) -> bool:
    scale = np.linalg.norm(applied)
    if scale == 0.0:
        return True
    return float(np.linalg.norm(k_ff @ solution - applied)) <= _RESIDUAL_TOLERANCE * scale


def _under_constrained(detail: str) -> SolverError:
    return SolverError(
        "The model is under-constrained: part of it can still move or spin freely "
        f"({detail}). Check that the fixtures remove all six rigid-body motions."
    )


def assemble_stiffness(mesh: TetMesh, material: Material) -> sp.csr_matrix:
    grads, volumes = _shape_gradients(mesh)
    b = _strain_displacement(grads)
    d = constitutive_matrix(material)

    # Ke = V * B.T D B, evaluated for every element at once.
    ke = volumes[:, None, None] * np.einsum("eji,jk,ekl->eil", b, d, b)

    dofs = _element_dofs(mesh.tets)
    rows = np.repeat(dofs, 12, axis=1).ravel()
    cols = np.tile(dofs, (1, 12)).ravel()
    n_dof = 3 * mesh.node_count
    return sp.coo_matrix((ke.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()


def von_mises(stress: NDArray[np.float64]) -> NDArray[np.float64]:
    sxx, syy, szz, sxy, syz, szx = stress.T
    return np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + szx**2)
    )


class LinearStaticSolver(Solver):
    name = "linear-static-tet4"

    def solve(self, mesh: TetMesh, case: LoadCase) -> SolveOutput:
        started = time.perf_counter()
        warnings: list[str] = []

        n_dof = 3 * mesh.node_count
        forces = np.zeros(n_dof, dtype=np.float64)
        for load in case.loads:
            nodes = select_nodes(mesh, load.where)
            nodal, warning = distribute_force(
                mesh, nodes, np.asarray(load.force_n, dtype=np.float64)
            )
            if warning:
                warnings.append(f"{load.name or 'Load'}: {warning}")
            np.add.at(forces, _dof_indices(nodes), nodal.ravel())

        fixed = np.unique(
            np.concatenate(
                [
                    _dof_indices(select_nodes(mesh, fixture.where), fixture.dofs)
                    for fixture in case.fixtures
                ]
            )
        )
        free = np.setdiff1d(np.arange(n_dof), fixed)
        if len(free) == 0:
            raise SolverError("Every degree of freedom is fixed; there is nothing to solve")

        stiffness = assemble_stiffness(mesh, case.material)
        k_ff = stiffness[free][:, free].tocsc()

        displacements = np.zeros(n_dof, dtype=np.float64)
        applied = forces[free]
        try:
            with warnings_module.catch_warnings():
                # A singular K raises MatrixRankWarning rather than failing.
                warnings_module.simplefilter("error", spla.MatrixRankWarning)
                solution = spla.spsolve(k_ff, applied)
        except (RuntimeError, spla.MatrixRankWarning) as exc:
            raise _under_constrained(str(exc)) from exc

        # SuperLU can return a finite but meaningless vector for a near-singular
        # system, so trust the residual rather than the absence of NaNs.
        if not np.all(np.isfinite(solution)) or not _residual_is_small(k_ff, solution, applied):
            raise _under_constrained("the solution does not satisfy equilibrium")
        displacements[free] = solution

        mises = self._recover_stress(mesh, case.material, displacements)
        result = self._summarise(
            mesh, case, displacements, mises, warnings, time.perf_counter() - started
        )
        return SolveOutput(
            result=result, displacements=displacements.reshape(-1, 3), von_mises=mises
        )

    def _recover_stress(
        self, mesh: TetMesh, material: Material, displacements: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        grads, _ = _shape_gradients(mesh)
        b = _strain_displacement(grads)
        element_u = displacements[_element_dofs(mesh.tets)]
        # Tet4 strain is constant over the element, so one evaluation is exact.
        strain = np.einsum("eij,ej->ei", b, element_u)
        stress = strain @ constitutive_matrix(material).T
        return von_mises(stress)

    def _summarise(
        self,
        mesh: TetMesh,
        case: LoadCase,
        displacements: NDArray[np.float64],
        mises: NDArray[np.float64],
        warnings: list[str],
        seconds: float,
    ) -> StaticResult:
        magnitudes = np.linalg.norm(displacements.reshape(-1, 3), axis=1)
        peak_node = int(np.argmax(magnitudes))
        peak_element = int(np.argmax(mises))
        peak_stress = float(mises[peak_element])

        yield_strength = case.material.yield_strength_mpa
        fos = yield_strength / peak_stress if peak_stress > 0.0 else float("inf")

        volume_mm3 = mesh.volume
        return StaticResult(
            max_displacement_mm=float(magnitudes[peak_node]),
            max_displacement_node=peak_node,
            max_von_mises_mpa=peak_stress,
            max_von_mises_element=peak_element,
            factor_of_safety=fos,
            yields=peak_stress >= yield_strength,
            mass_kg=volume_mm3 * 1e-9 * case.material.density_kg_m3,
            volume_mm3=volume_mm3,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            solve_seconds=seconds,
            warnings=warnings,
        )
