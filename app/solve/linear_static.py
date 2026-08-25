"""Linear static structural FEA on 4-node and 10-node tetrahedra.

Small-strain, isotropic, linear elastic. This is the ground-truth baseline the
PRD calls for: unremarkable, well-understood physics whose numbers can be
checked against closed-form solutions.

Tet4 elements are constant-strain, so they are noticeably stiff in bending.
Tet10 (quadratic) elements capture bending much more accurately per element,
and are selected automatically when the mesh provides midside nodes.
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

# Above this DOF count, use CG with an ILU preconditioner instead of direct
# spsolve. Direct solve is O(n^1.5) in memory; CG is O(n) with a good
# preconditioner. The crossover favours CG once memory becomes the bottleneck.
_ITERATIVE_THRESHOLD_DOF = 100_000

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
    dets = np.asarray(np.linalg.det(jacobians), dtype=np.float64)

    degenerate = np.abs(dets) < _MIN_JACOBIAN
    if degenerate.any():
        raise SolverError(
            f"{int(degenerate.sum())} element(s) have zero volume; the mesh is degenerate"
        )

    # Chain rule through the natural coordinates gives dN/dx = dN/dxi @ inv(J).T
    inv_j = np.linalg.inv(jacobians)
    grads = np.asarray(_DN_DXI @ np.transpose(inv_j, (0, 2, 1)), dtype=np.float64)
    volumes = np.asarray(np.abs(dets) / 6.0, dtype=np.float64)
    return grads, volumes


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
    residual = np.linalg.norm(k_ff @ solution - applied)
    return bool(float(residual) <= _RESIDUAL_TOLERANCE * scale)


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


def assemble_stiffness_tet10(
    nodes: NDArray[np.float64],
    tets10: NDArray[np.int64],
    material: Material,
) -> sp.csr_matrix:
    """Assemble the global stiffness matrix for quadratic (tet10) elements.

    Each tet10 element has 10 nodes -> 30 local DOFs.
    Node ordering follows Gmsh convention:
      [v0, v1, v2, v3, m01, m12, m20, m03, m13, m23]
    """
    n_nodes = len(nodes)
    n_elem = len(tets10)
    n_dof = 3 * n_nodes
    n_ldof = 30

    p = nodes[tets10]  # shape (n_elem, 10, 3)
    d = constitutive_matrix(material)

    # For each element, compute B(ξ,η,ζ=1-ξ-η-ω), integrate at 4-point Gauss.
    # Gauss points for tets: α = (5-sqrt(5))/20, β = (5+3sqrt(5))/20.
    alpha = (5.0 - np.sqrt(5.0)) / 20.0
    beta = (5.0 + 3.0 * np.sqrt(5.0)) / 20.0
    gauss_pts = [
        (alpha, alpha, alpha),
        (beta, alpha, alpha),
        (alpha, beta, alpha),
        (alpha, alpha, beta),
    ]
    gauss_w = 1.0 / 24.0  # equal weights sum to 1/6 (tet volume)

    ke_all = np.zeros((n_elem, n_ldof, n_ldof), dtype=np.float64)

    for xi, eta, zeta in gauss_pts:
        dn_dxi = _tet10_shape_gradients_natural(xi, eta, zeta)  # (10, 3)
        # Compute Jacobian: J[i][k] = sum_j dn_dxi[j,i] * p[e,j,k]
        jac = np.einsum("ji,ejk->eik", dn_dxi, p)  # (n_elem, 3, 3)
        det_jac = np.linalg.det(jac)
        if (np.abs(det_jac) < _MIN_JACOBIAN).any():
            raise SolverError("Degenerate tet10 element detected during assembly")

        inv_jac = np.linalg.inv(jac)
        # Chain rule: dN/dx = dN/dxi @ inv(J).T, shape (n_elem, 10, 3)
        grads = np.einsum("ij,ejk->eik", dn_dxi, inv_jac.transpose(0, 2, 1))

        # Build B matrices (6 x 30) for all elements
        b = np.zeros((n_elem, 6, 30))
        for node_idx in range(10):
            col_base = 3 * node_idx
            gx = grads[:, node_idx, 0]
            gy = grads[:, node_idx, 1]
            gz = grads[:, node_idx, 2]
            b[:, 0, col_base + 0] = gx
            b[:, 1, col_base + 1] = gy
            b[:, 2, col_base + 2] = gz
            b[:, 3, col_base + 0] = gy
            b[:, 3, col_base + 1] = gx
            b[:, 4, col_base + 1] = gz
            b[:, 4, col_base + 2] = gy
            b[:, 5, col_base + 0] = gz
            b[:, 5, col_base + 2] = gx

        ke_gauss = gauss_w * det_jac[:, None, None] * np.einsum("eji,jk,ekl->eil", b, d, b)
        ke_all += ke_gauss

    dofs = (tets10[:, :, None] * 3 + np.arange(3)[None, None, :]).reshape(n_elem, 30)
    rows = np.repeat(dofs, 30, axis=1).ravel()
    cols = np.tile(dofs, (1, 30)).ravel()
    return sp.coo_matrix((ke_all.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()


def _tet10_shape_gradients_natural(xi: float, eta: float, zeta: float) -> NDArray[np.float64]:
    """dN/d(ξ,η,ζ) at natural coordinates for the 10-node tet.

    Returns shape (10, 3). Ordering matches the node ordering above.
    """
    omega = 1.0 - xi - eta - zeta
    # Corner nodes: N_i = L_i * (2*L_i - 1) where L = (omega, xi, eta, zeta).
    # Mid-side nodes: N_ij = 4*L_i*L_j.
    # Full derivatives computed symbolically below.
    L = [omega, xi, eta, zeta]
    corners = [(0, omega), (1, xi), (2, eta), (3, zeta)]
    mids = [(4, 0, 1), (5, 1, 2), (6, 2, 0), (7, 0, 3), (8, 1, 3), (9, 2, 3)]
    grad = np.zeros((10, 3))

    # ∂N_corner/∂L_k = 4*L_i - 1 when k==i else 0; then chain through L→ξ,η,ζ.
    # ∂N_mid/∂L_i = 4*L_j; ∂N_mid/∂L_j = 4*L_i; others 0.
    dL_dnat = np.array([[-1,-1,-1],[1,0,0],[0,1,0],[0,0,1]], dtype=np.float64)

    for i, (corner_id, Li) in enumerate(corners):
        for nat_dir in range(3):
            total = 0.0
            for L_idx in range(4):
                if L_idx == corner_id:
                    dNi_dLj = 4.0 * L[L_idx] - 1.0
                else:
                    dNi_dLj = 0.0
                total += dNi_dLj * dL_dnat[L_idx, nat_dir]
            grad[corner_id, nat_dir] = total

    for node_id, li, lj in mids:
        for nat_dir in range(3):
            total = 0.0
            for L_idx in range(4):
                if L_idx == li:
                    dNi_dLj = 4.0 * L[lj]
                elif L_idx == lj:
                    dNi_dLj = 4.0 * L[li]
                else:
                    dNi_dLj = 0.0
                total += dNi_dLj * dL_dnat[L_idx, nat_dir]
            grad[node_id, nat_dir] = total

    return grad


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
        k_ff.eliminate_zeros()

        displacements = np.zeros(n_dof, dtype=np.float64)
        applied = forces[free]

        if n_dof > _ITERATIVE_THRESHOLD_DOF:
            solution = self._solve_iterative(k_ff, applied)
        else:
            solution = self._solve_direct(k_ff, applied)

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

    @staticmethod
    def _solve_direct(k_ff: sp.csc_matrix, applied: NDArray[np.float64]) -> NDArray[np.float64]:
        try:
            with warnings_module.catch_warnings():
                # A singular K raises MatrixRankWarning rather than failing.
                warnings_module.simplefilter("error", spla.MatrixRankWarning)
                solution = spla.spsolve(k_ff, applied, permc_spec="COLAMD", use_umfpack=False)
        except (RuntimeError, spla.MatrixRankWarning) as exc:
            raise _under_constrained(str(exc)) from exc
        return solution

    @staticmethod
    def _solve_iterative(
        k_ff: sp.csc_matrix, applied: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        ilu = spla.spilu(k_ff, drop_tol=1e-5, fill_factor=15.0, permc_spec="COLAMD")
        precond = spla.LinearOperator(k_ff.shape, matvec=ilu.solve)
        solution, info = spla.cg(
            k_ff, applied, rtol=_RESIDUAL_TOLERANCE, maxiter=5000, M=precond
        )
        if info != 0:
            raise SolverError(f"Conjugate gradient failed to converge (info={info})")
        return solution

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
