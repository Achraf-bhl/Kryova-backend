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

from app import observe
from app.mesh.types import TET10_EDGES, TetMesh
from app.solve.base import SolveOutput, Solver
from app.solve.loads import assemble_loads
from app.solve.postprocess import nodal_average, summarise_static
from app.solve.selection import select_nodes
from app.solve.types import LoadCase, Material, SolverError

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

#: Where each of a tet10's ten nodes sits in natural coordinates, in the local
#: order the connectivity uses: the four corners, then the midsides in
#: `TET10_EDGES` order. Derived from that table rather than typed out, so a mesh
#: whose midside ordering changed could not leave this silently describing the
#: old one — the stress would then be evaluated at the wrong point of the right
#: element, which is a plausible wrong number and not an error.
_TET10_CORNERS_NATURAL: tuple[tuple[float, float, float], ...] = (
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)
def _midside_natural(a: int, b: int) -> tuple[float, float, float]:
    first, second = _TET10_CORNERS_NATURAL[a], _TET10_CORNERS_NATURAL[b]
    return (
        0.5 * (first[0] + second[0]),
        0.5 * (first[1] + second[1]),
        0.5 * (first[2] + second[2]),
    )


_TET10_NATURAL_NODES: tuple[tuple[float, float, float], ...] = (
    *_TET10_CORNERS_NATURAL,
    *(_midside_natural(a, b) for a, b in TET10_EDGES),
)

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


def _temperature_change(
    mesh: TetMesh, case: LoadCase, temperatures: NDArray[np.float64] | None
) -> float | NDArray[np.float64] | None:
    """The temperature change this solve applies, per element or uniformly.

    **A prescribed field is a solver argument rather than a field on
    `LoadCase`, and that is deliberate.** A case is stored as JSONB on the job
    row and is meant to be read by a person; one number per node is data the
    size of the mesh, and putting it there would make every saved load case
    unreadable and every row enormous — to say nothing of a field that no longer
    matches the mesh it was written for. It is passed alongside the mesh it
    belongs to, or not at all.

    Nodal values are averaged onto their element's corners, which is the
    approximation the assembly already makes: `thermal_load` integrates a
    constant strain per element in the uniform case too.

    Refused rather than truncated when the length is wrong, because a field one
    node short would silently shift every temperature by one node and produce a
    plausible, wrong answer.
    """
    if temperatures is None:
        return case.delta_t_k if case.delta_t_k else None
    field = np.asarray(temperatures, dtype=np.float64).reshape(-1)
    if len(field) != mesh.node_count:
        raise SolverError(
            f"The temperature field has {len(field)} values and the mesh has "
            f"{mesh.node_count} nodes. A field is bound to the mesh it was evaluated "
            "on; re-evaluate it on this one rather than padding or truncating it."
        )
    if case.delta_t_k:
        raise SolverError(
            "This case carries a uniform delta_t_k and a temperature field was also "
            "supplied. Adding them would silently double-count the expansion — give "
            "one or the other, and fold any uniform offset into the field."
        )
    return field[mesh.tets[:, :4]].mean(axis=1)


class LinearStaticSolver(Solver):
    name = "linear-static"

    def solve(
        self,
        mesh: TetMesh,
        case: LoadCase,
        temperatures: NDArray[np.float64] | None = None,
    ) -> SolveOutput:
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
        delta_t = _temperature_change(mesh, case, temperatures)
        if delta_t is not None:
            from app.solve.thermal import thermal_load

            forces += thermal_load(mesh, case.material, delta_t)

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

        # Assembly and factorisation get separate spans on purpose: a slow
        # assembly and a slow solve have different fixes — one is element count,
        # the other is bandwidth and fill-in — and a single span over both cannot
        # tell them apart, which is the whole reason to time anything.
        with observe.span(
            "solve.linear_static",
            nodes=mesh.node_count,
            elements=mesh.tet_count,
            degrees_of_freedom=int(n_dof),
        ) as timing:
            timing.set("stage", "assemble")
            stiffness = assemble_stiffness(mesh, case.material)
            k_ff = stiffness[free][:, free].tocsc()
            k_ff.eliminate_zeros()

            displacements = np.zeros(n_dof, dtype=np.float64)
            applied = forces[free]

            timing.set("stage", "factorise")
            if n_dof > _ITERATIVE_THRESHOLD_DOF:
                timing.set("method", "iterative")
                solution = self._solve_iterative(k_ff, applied)
            else:
                timing.set("method", "direct")
                solution = self._solve_direct(k_ff, applied)

        if not np.all(np.isfinite(solution)) or not _residual_is_small(k_ff, solution, applied):
            raise _under_constrained("the solution does not satisfy equilibrium")
        displacements[free] = solution

        element_stress = self._recover_stress(
            mesh, case.material, displacements, delta_t_k=delta_t
        )
        mises = von_mises(element_stress)
        # Shared with every other Solver rather than computed here: two
        # solvers that summarised their own results would be free to mean
        # different things by "factor of safety", and 6.5 compares them.
        result = summarise_static(
            mesh, case, displacements, mises, warnings, time.perf_counter() - started
        )
        return SolveOutput(
            result=result,
            displacements=displacements.reshape(-1, 3),
            von_mises=mises,
            # Averaged onto the nodes rather than reported per element. The
            # headline peak in `result` stays the raw element value — smoothing
            # a concentration out of the number an engineer sizes to would be a
            # different and much worse decision — but a stress *at a named
            # point* is a nodal question, and CalculiX answers it at nodes too,
            # so the oracle compares like with like.
            nodal_stress=self._recover_nodal_stress(
                mesh, case.material, displacements, delta_t_k=delta_t
            ),
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

    def _stress_at(
        self,
        mesh: TetMesh,
        material: Material,
        displacements: NDArray[np.float64],
        natural: tuple[float, float, float],
        delta_t_k: float | NDArray[np.float64] | None,
    ) -> NDArray[np.float64]:
        """The stress tensor of every element at one natural coordinate.

        (n_elements, 6), SXX SYY SZZ SXY SYZ SZX — the tensor rather than von
        Mises, because the caller needs both and the invariant throws away the
        six numbers a signed component reader needs.
        """
        if mesh.midside is None:
            # Tet4 strain is constant over the element, so the natural point is
            # irrelevant and one evaluation is exact.
            grads, _ = _shape_gradients(mesh)
        else:
            grads, _ = _mapped_gradients(
                mesh.nodes[mesh.connectivity], _tet10_shape_gradients(*natural)
            )
        b = _strain_displacement(grads)
        element_u = displacements[_element_dofs(mesh.connectivity)]
        strain = np.einsum("eij,ej->ei", b, element_u)
        stress = strain @ constitutive_matrix(material).T
        if delta_t_k is not None:
            # Only the *mechanical* part of the strain carries stress. Skipping
            # this subtraction reports the stress of a part that was free to
            # expand, which for a restrained bar is the wrong sign as well as
            # the wrong size -- see app/solve/thermal.py.
            from app.solve.thermal import thermal_stress_correction

            stress = stress - thermal_stress_correction(material, delta_t_k)
        return stress

    def _recover_stress(
        self,
        mesh: TetMesh,
        material: Material,
        displacements: NDArray[np.float64],
        delta_t_k: float | NDArray[np.float64] | None = None,
    ) -> NDArray[np.float64]:
        """The stress reported *per element*, sampled at the centroid.

        The centroid is the element's superconvergent point: sampling at a face
        or a corner instead reads the extrapolated tail of the element's own
        approximation and overstates the peak, and this is the number the
        headline peak stress and the factor of safety are computed from.
        """
        return self._stress_at(mesh, material, displacements, _CENTROID, delta_t_k)

    def _recover_nodal_stress(
        self,
        mesh: TetMesh,
        material: Material,
        displacements: NDArray[np.float64],
        delta_t_k: float | NDArray[np.float64] | None = None,
    ) -> NDArray[np.float64]:
        """The stress reported *per node*, (n_nodes, 6).

        **Evaluated at each node's own natural coordinate and then averaged, not
        by averaging centroid values.** The two sound alike and differ by the
        thing a plate benchmark measures: a tet10's strain is linear, so on a
        plate in bending the centroid value is the stress halfway between the
        surface and the neutral axis. Spreading it to the nodes reports about
        75% of the surface stress on a mesh four elements thick — and because the
        error shrinks with refinement, it reads as a converging answer rather
        than as a systematic offset. Evaluating at the node recovers the linear
        variation the element already carries.

        For tet4 the strain is constant, so this necessarily reduces to
        averaging the element values: a constant-strain element has no
        through-element variation to recover, which is the same reason it cannot
        represent bending in the first place.
        """
        connectivity = mesh.connectivity
        if mesh.midside is None:
            return nodal_average(
                mesh, self._stress_at(mesh, material, displacements, _CENTROID, delta_t_k)
            )

        totals = np.zeros((mesh.node_count, 6), dtype=np.float64)
        counts = np.zeros(mesh.node_count, dtype=np.int64)
        for local, natural in enumerate(_TET10_NATURAL_NODES):
            stress = self._stress_at(mesh, material, displacements, natural, delta_t_k)
            np.add.at(totals, connectivity[:, local], stress)
            np.add.at(counts, connectivity[:, local], 1)
        divisor = counts[:, None]
        return np.divide(totals, divisor, out=np.zeros_like(totals), where=divisor > 0)
