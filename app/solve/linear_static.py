"""Linear static structural FEA on 4-node and 10-node tetrahedra.

Small-strain, isotropic, linear elastic. This is the ground-truth baseline the
PRD calls for: unremarkable, well-understood physics whose numbers can be
checked against closed-form solutions.

Tet4 elements are constant-strain, so they are noticeably stiff in bending: a
cantilever meshed with them can come back less than half as flexible as the
beam it represents. Tet10 (quadratic) elements have a linear strain field and
recover that bending behaviour at the same element count, at roughly 2.5x the
degrees of freedom. `solve` dispatches on what the mesh carries -- a mesh with
midside nodes is assembled and post-processed as tet10 throughout -- and the
mesher emits them when asked for `element_order=2`.

Surface loads follow the element order too: `selection.distribute_force` puts a
quadratic face's load on its midside nodes, which is where the face shape
functions integrate to. Spreading it over the corners instead is only
statically equivalent and overstates the peak stress next to a loaded face by
around 20%.
"""

import time
import warnings as warnings_module
from collections.abc import Sequence

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray

from app.mesh.types import TET10_EDGES, TetMesh
from app.solve.base import SolveOutput, Solver
from app.solve.loads import assemble_loads
from app.solve.selection import select_nodes
from app.solve.types import LoadCase, Material, SolverError, StaticResult

# Derivatives of the tet4 shape functions with respect to natural coordinates.
_DN_DXI = np.array([[-1.0, -1.0, -1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])

# Derivatives of the four barycentric coordinates with respect to (xi, eta,
# zeta), where L = (1 - xi - eta - zeta, xi, eta, zeta). Numerically the same
# table as _DN_DXI; kept separate because it means something different.
_DL_DNAT = _DN_DXI

# Four-point Gauss rule on the reference tetrahedron, exact to degree 2. The
# tet10 integrand B^T D B is quadratic in the natural coordinates for a
# straight-edged element, so this rule integrates it exactly rather than
# approximately.
_TET_GAUSS_ALPHA = (5.0 - np.sqrt(5.0)) / 20.0
_TET_GAUSS_BETA = (5.0 + 3.0 * np.sqrt(5.0)) / 20.0
_TET_GAUSS_POINTS = (
    (_TET_GAUSS_ALPHA, _TET_GAUSS_ALPHA, _TET_GAUSS_ALPHA),
    (_TET_GAUSS_BETA, _TET_GAUSS_ALPHA, _TET_GAUSS_ALPHA),
    (_TET_GAUSS_ALPHA, _TET_GAUSS_BETA, _TET_GAUSS_ALPHA),
    (_TET_GAUSS_ALPHA, _TET_GAUSS_ALPHA, _TET_GAUSS_BETA),
)
# The four equal weights sum to the reference tetrahedron's volume, 1/6.
_TET_GAUSS_WEIGHT = 1.0 / 24.0

_CENTROID = (0.25, 0.25, 0.25)

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
    """B matrices, shape (n_elem, 6, 3 * nodes_per_element), engineering shears.

    Shared by both element orders: the layout only depends on how many nodes an
    element has, which is `grads.shape[1]`.
    """
    n, per_element = grads.shape[0], grads.shape[1]
    b = np.zeros((n, 6, 3 * per_element), dtype=np.float64)
    gx, gy, gz = grads[..., 0], grads[..., 1], grads[..., 2]
    for node in range(per_element):
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


def _element_dofs(connectivity: NDArray[np.int64]) -> NDArray[np.int64]:
    """Global DOF index for each element-local DOF, shape (n_elem, 3 * nodes)."""
    per_element = connectivity.shape[1]
    return (connectivity[:, :, None] * 3 + np.arange(3)[None, None, :]).reshape(
        len(connectivity), 3 * per_element
    )


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
    """Global stiffness matrix, for whichever element order the mesh carries."""
    if mesh.midside is not None:
        return assemble_stiffness_tet10(mesh, material)
    return assemble_stiffness_tet4(mesh, material)


def assemble_stiffness_tet4(mesh: TetMesh, material: Material) -> sp.csr_matrix:
    grads, volumes = _shape_gradients(mesh)
    b = _strain_displacement(grads)
    d = constitutive_matrix(material)

    # Ke = V * B.T D B, evaluated for every element at once. Tet4 strain is
    # constant over the element, so one evaluation integrates it exactly.
    ke = volumes[:, None, None] * np.einsum("eji,jk,ekl->eil", b, d, b)
    return _scatter(mesh, _element_dofs(mesh.tets), ke)


def assemble_stiffness_tet10(mesh: TetMesh, material: Material) -> sp.csr_matrix:
    """Global stiffness for quadratic tets: 10 nodes, 30 local DOFs each.

    Unlike tet4, strain varies over the element, so the element matrix is
    integrated numerically -- at the four Gauss points that make the rule exact
    for the quadratic integrand a straight-edged tet10 produces.
    """
    if mesh.midside is None:
        raise SolverError("mesh has no midside nodes; it is not a tet10 mesh")

    connectivity = mesh.connectivity
    points = mesh.nodes[connectivity]  # (n_elem, 10, 3)
    d = constitutive_matrix(material)
    ke = np.zeros((len(connectivity), 30, 30), dtype=np.float64)

    for point in _TET_GAUSS_POINTS:
        grads, detj = _mapped_gradients(points, _tet10_shape_gradients(*point))
        b = _strain_displacement(grads)
        ke += _TET_GAUSS_WEIGHT * detj[:, None, None] * np.einsum("eji,jk,ekl->eil", b, d, b)

    return _scatter(mesh, _element_dofs(connectivity), ke)


def _tet10_shape_gradients(xi: float, eta: float, zeta: float) -> NDArray[np.float64]:
    """dN/d(xi, eta, zeta) for the 10-node tet, shape (10, 3).

    In barycentric coordinates L = (1 - xi - eta - zeta, xi, eta, zeta) the
    shape functions are N_i = L_i (2 L_i - 1) at the corners and N_ab = 4 L_a L_b
    at the midside of edge (a, b), so the derivatives fall straight out of the
    product rule. Midside ordering is `TET10_EDGES`, the same table the mesher
    fills in.
    """
    lam = np.array([1.0 - xi - eta - zeta, xi, eta, zeta], dtype=np.float64)
    grad = np.zeros((10, 3), dtype=np.float64)
    for corner in range(4):
        grad[corner] = (4.0 * lam[corner] - 1.0) * _DL_DNAT[corner]
    for local, (a, b) in enumerate(TET10_EDGES, start=4):
        grad[local] = 4.0 * (lam[b] * _DL_DNAT[a] + lam[a] * _DL_DNAT[b])
    return grad


def _mapped_gradients(
    points: NDArray[np.float64], dn_dnat: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Physical dN/dx and |det J| for every element at one natural point.

    `points` is (n_elem, nodes, 3); `dn_dnat` is (nodes, 3).
    """
    jacobian = np.einsum("ji,ejk->eik", dn_dnat, points)  # J[i,k] = dx_k/dxi_i
    determinant = np.abs(np.asarray(np.linalg.det(jacobian), dtype=np.float64))
    degenerate = determinant < _MIN_JACOBIAN
    if degenerate.any():
        raise SolverError(
            f"{int(degenerate.sum())} element(s) have zero volume; the mesh is degenerate"
        )
    inverse = np.linalg.inv(jacobian)
    # Chain rule: dN/dx = dN/dxi @ inv(J).T
    grads = np.asarray(np.einsum("ij,ekj->eik", dn_dnat, inverse), dtype=np.float64)
    return grads, determinant


def _scatter(mesh: TetMesh, dofs: NDArray[np.int64], ke: NDArray[np.float64]) -> sp.csr_matrix:
    """Sum element matrices into the global sparse stiffness matrix."""
    width = dofs.shape[1]
    rows = np.repeat(dofs, width, axis=1).ravel()
    cols = np.tile(dofs, (1, width)).ravel()
    n_dof = 3 * mesh.node_count
    return sp.coo_matrix((ke.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()


def von_mises(stress: NDArray[np.float64]) -> NDArray[np.float64]:
    sxx, syy, szz, sxy, syz, szx = stress.T
    return np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + szx**2)
    )


class LinearStaticSolver(Solver):
    name = "linear-static"

    def solve(self, mesh: TetMesh, case: LoadCase) -> SolveOutput:
        started = time.perf_counter()
        warnings: list[str] = []

        n_dof = 3 * mesh.node_count
        # Every load type -- force, pressure, moment, bearing, gravity,
        # centrifugal -- resolves to a nodal force vector in `app.solve.loads`.
        # The density comes from the case's material because the body loads need
        # it and nothing else in the assembly does.
        forces, load_warnings = assemble_loads(
            mesh, case.loads, case.material.density_kg_m3
        )
        warnings.extend(load_warnings)

        # Restrained thermal expansion is a load like any other, so it is added
        # here rather than solved separately: a part that is both heated and
        # pushed has one displacement field, not two to superpose by hand.
        if case.delta_t_k:
            from app.solve.thermal import thermal_load

            forces += thermal_load(mesh, case.material, case.delta_t_k)

        fixed = np.unique(
            np.concatenate(
                [
                    _dof_indices(select_nodes(mesh, fixture.where), fixture.held)
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

        mises = self._recover_stress(
            mesh, case.material, displacements, delta_t_k=case.delta_t_k
        )
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
    def _solve_iterative(k_ff: sp.csc_matrix, applied: NDArray[np.float64]) -> NDArray[np.float64]:
        """Preconditioned CG for the systems too large to factorise directly.

        The preconditioner is Jacobi (inverse diagonal), NOT incomplete-LU.
        `spilu` looks like the stronger choice and is the obvious thing to
        reach for, but SuperLU's ILUTP applies partial pivoting and a column
        permutation, so the operator it produces is **not symmetric** -- and
        CG's convergence proof requires an SPD preconditioner. The asymmetry
        grows with element order (measured: 1.6e-3 on tet4, 3.6e-2 on tet10),
        which is why linear meshes appeared to work and quadratic ones did not.

        Measured on a 22,308-DOF tet10 cantilever:

            spilu   diverged at maxiter, rel-residual 1.9e-3, 300 s
            none    converged,           rel-residual 9.4e-9,   6.4 s
            Jacobi  converged,           rel-residual 9.9e-9,   4.7 s

        So the ILU path was not merely slower than nothing -- it turned every
        simulation above `_ITERATIVE_THRESHOLD_DOF` into a five-minute failure.
        K is SPD here (symmetric assembly, and the free-DOF restriction of a
        properly constrained stiffness matrix is positive definite), so its
        diagonal is strictly positive and Jacobi is SPD by construction.
        """
        diagonal = k_ff.diagonal()
        if not np.all(diagonal > 0.0):
            # A non-positive diagonal means the matrix is not SPD, so CG does
            # not apply at all. That is an under-constrained model, not a
            # numerical hiccup -- say so rather than iterating to nowhere.
            raise _under_constrained("the stiffness matrix is not positive definite")

        inverse_diagonal = 1.0 / diagonal
        precond = spla.LinearOperator(
            k_ff.shape, matvec=lambda x: inverse_diagonal * x, dtype=np.float64
        )
        solution, info = spla.cg(k_ff, applied, rtol=_RESIDUAL_TOLERANCE, maxiter=5000, M=precond)
        if info != 0:
            raise SolverError(
                f"Conjugate gradient failed to converge (info={info}). The model may be "
                "under-constrained, or the mesh may need refining."
            )
        return solution

    def _recover_stress(
        self,
        mesh: TetMesh,
        material: Material,
        displacements: NDArray[np.float64],
        delta_t_k: float | None = None,
    ) -> NDArray[np.float64]:
        if mesh.midside is None:
            grads, _ = _shape_gradients(mesh)
            # Tet4 strain is constant over the element, so one evaluation is exact.
        else:
            # Tet10 strain is linear, so it has to be sampled somewhere. The
            # centroid is the element's superconvergent point: sampling at a
            # face or a corner instead reads the extrapolated tail of the
            # element's own approximation and overstates the peak.
            grads, _ = _mapped_gradients(
                mesh.nodes[mesh.connectivity], _tet10_shape_gradients(*_CENTROID)
            )
        b = _strain_displacement(grads)
        element_u = displacements[_element_dofs(mesh.connectivity)]
        strain = np.einsum("eij,ej->ei", b, element_u)
        stress = strain @ constitutive_matrix(material).T
        if delta_t_k:
            # Only the *mechanical* part of the strain carries stress. Skipping
            # this subtraction reports the stress of a part that was free to
            # expand, which for a restrained bar is the wrong sign as well as
            # the wrong size -- see app/solve/thermal.py.
            from app.solve.thermal import thermal_stress_correction

            stress = stress - thermal_stress_correction(material, delta_t_k)
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
