"""Linear (eigenvalue) buckling.

Answers "how much more of this load before it goes unstable", by solving

    K phi = -lambda Kg(sigma) phi

where `Kg` is the geometric stiffness built from the stress state the applied
loads produce. `lambda` multiplies the loads: 3.2 means the structure buckles at
3.2x what was applied, and anything below 1 means it has already gone.

**This is the linear answer, and it is an upper bound.** It assumes the
pre-buckling deflection is negligible, the material stays elastic, and the
structure has no imperfections -- none of which is true of a real part. A real
column buckles below its Euler load, and a thin shell can go at a fraction of
it. The number is a screening tool, which is why `BucklingResult` reports the
factor rather than a pass/fail verdict.

**Why the eigenproblem is posed as `-Kg phi = mu K phi` rather than the other
way round.** The natural statement puts `Kg` on the right, and `Kg` is
indefinite -- compressive regions make it negative, tensile ones positive -- so
the generalised symmetric eigensolver, which needs a positive-definite matrix
there, cannot take it. `K` after constraints *is* positive definite, so swapping
the sides gives a well-posed problem whose largest eigenvalues are the
reciprocals of the smallest load factors. `lambda = 1/mu`, and the sign of `mu`
carries which direction of load buckles.
"""

from __future__ import annotations

import time

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.base import SolveOutput
from app.solve.linear_static import (
    _TET_GAUSS_POINTS,
    _TET_GAUSS_WEIGHT,
    LinearStaticSolver,
    _dof_indices,
    _element_dofs,
    _mapped_gradients,
    _scatter,
    _shape_gradients,
    _strain_displacement,
    _tet10_shape_gradients,
    assemble_stiffness,
    constitutive_matrix,
)
from app.solve.selection import select_nodes
from app.solve.types import BucklingCase, BucklingResult, LoadCase, Material, SolverError

#: Voigt order is [xx, yy, zz, xy, yz, zx]; this maps it to the 3x3 tensor.
_VOIGT_TO_TENSOR = (
    (0, 3, 5),
    (3, 1, 4),
    (5, 4, 2),
)

#: A load factor smaller than this is numerical noise around an eigenvalue that
#: is really infinite -- a direction the structure simply does not buckle in.
_MIN_FACTOR = 1e-9


def _stress_tensors(voigt: NDArray[np.float64]) -> NDArray[np.float64]:
    """(n_elem, 6) Voigt stress -> (n_elem, 3, 3) symmetric tensors."""
    tensor = np.empty((len(voigt), 3, 3), dtype=np.float64)
    for row in range(3):
        for column in range(3):
            tensor[:, row, column] = voigt[:, _VOIGT_TO_TENSOR[row][column]]
    return tensor


def _gradient_operator(grads: NDArray[np.float64]) -> NDArray[np.float64]:
    """G, shape (n_elem, 9, 3 * nodes), with `G u = [du_x/dx, du_x/dy, ...]`.

    Nine rows because the geometric stiffness works on the full displacement
    gradient, not on the symmetric strain the constitutive law uses: it is
    rotation that destabilises a compressed member, and the symmetric part
    throws rotation away.
    """
    n_elem, nodes, _ = grads.shape
    operator = np.zeros((n_elem, 9, 3 * nodes), dtype=np.float64)
    for component in range(3):
        for direction in range(3):
            operator[:, 3 * component + direction, component::3] = grads[:, :, direction]
    return operator


def _expand_stress(tensors: NDArray[np.float64]) -> NDArray[np.float64]:
    """(n_elem, 3, 3) -> (n_elem, 9, 9), the stress repeated on the diagonal blocks."""
    n_elem = len(tensors)
    expanded = np.zeros((n_elem, 9, 9), dtype=np.float64)
    for component in range(3):
        block = slice(3 * component, 3 * component + 3)
        expanded[:, block, block] = tensors
    return expanded


def assemble_geometric_stiffness(
    mesh: TetMesh, material: Material, displacements: NDArray[np.float64]
) -> sp.csr_matrix:
    """Geometric stiffness for the stress state `displacements` produces.

    `displacements` is flat (3 * n_nodes,), the same shape the static solver
    returns internally.

    The stress is recomputed here at each integration point rather than reusing
    the static solver's per-element von Mises. Two reasons: von Mises is a
    scalar and this needs the tensor, and for tet10 the stress varies across the
    element, so a single centroid value would integrate the wrong field.
    """
    connectivity = mesh.connectivity
    d = constitutive_matrix(material)
    element_dofs = _element_dofs(connectivity)
    local = displacements[element_dofs]  # (n_elem, 3 * nodes)

    if mesh.midside is None:
        grads, volumes = _shape_gradients(mesh)
        b = _strain_displacement(grads)
        stress = np.einsum("ij,ejk,ek->ei", d, b, local)
        operator = _gradient_operator(grads)
        expanded = _expand_stress(_stress_tensors(stress))
        kg = volumes[:, None, None] * np.einsum(
            "eji,ejk,ekl->eil", operator, expanded, operator
        )
        return _scatter(mesh, element_dofs, kg)

    points = mesh.nodes[connectivity]
    size = 3 * connectivity.shape[1]
    kg = np.zeros((len(connectivity), size, size), dtype=np.float64)
    for point in _TET_GAUSS_POINTS:
        grads, detj = _mapped_gradients(points, _tet10_shape_gradients(*point))
        b = _strain_displacement(grads)
        stress = np.einsum("ij,ejk,ek->ei", d, b, local)
        operator = _gradient_operator(grads)
        expanded = _expand_stress(_stress_tensors(stress))
        kg += (
            _TET_GAUSS_WEIGHT
            * detj[:, None, None]
            * np.einsum("eji,ejk,ekl->eil", operator, expanded, operator)
        )
    return _scatter(mesh, element_dofs, kg)


class LinearBucklingSolver:
    """Eigenvalue buckling on top of one linear static solve."""

    name = "linear-buckling"

    def __init__(self, static: LinearStaticSolver | None = None) -> None:
        self._static = static or LinearStaticSolver()

    def solve(self, mesh: TetMesh, case: BucklingCase) -> tuple[BucklingResult, NDArray[np.float64], SolveOutput]:
        """Returns the result, the mode shapes (n_modes, n_nodes, 3), and the
        static run the factors are relative to.

        The static output comes back deliberately: a load factor means nothing
        without the load it multiplies, and the caller almost always wants to
        report the stress at the same time.
        """
        started = time.perf_counter()
        warnings: list[str] = []

        static_case = LoadCase(
            name=case.name,
            material=case.material,
            fixtures=case.fixtures,
            loads=case.loads,
        )
        static = self._static.solve(mesh, static_case)
        warnings.extend(static.result.warnings)

        n_dof = 3 * mesh.node_count
        fixed = np.unique(
            np.concatenate(
                [
                    _dof_indices(select_nodes(mesh, fixture.where), fixture.held)
                    for fixture in case.fixtures
                ]
            )
        )
        free = np.setdiff1d(np.arange(n_dof), fixed)

        stiffness = assemble_stiffness(mesh, case.material)[free][:, free].tocsc()
        geometric = assemble_geometric_stiffness(
            mesh, case.material, static.displacements.ravel()
        )[free][:, free].tocsc()

        wanted = min(case.modes, len(free) - 2)
        if wanted < 1:
            raise SolverError(
                "The model has too few free degrees of freedom to find a buckling mode. "
                "Refine the mesh or release a fixture."
            )
        if wanted < case.modes:
            warnings.append(f"Asked for {case.modes} modes; this mesh only supports {wanted}.")

        factors, shapes = self._factors(stiffness, geometric, free, n_dof, wanted, mesh)
        if not any(factor > 0.0 for factor in factors):
            warnings.append(
                "No positive load factor was found: the structure does not buckle under "
                "this load in this direction. Reverse the load to check the other."
            )

        volume = float(mesh.volume)
        result = BucklingResult(
            load_factors=factors,
            mass_kg=volume * 1e-9 * float(case.material.density_kg_m3),
            volume_mm3=volume,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            solve_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        return result, shapes, static

    @staticmethod
    def _factors(
        stiffness: sp.csc_matrix,
        geometric: sp.csc_matrix,
        free: NDArray[np.int64],
        n_dof: int,
        wanted: int,
        mesh: TetMesh,
    ) -> tuple[list[float], NDArray[np.float64]]:
        try:
            # Largest algebraic eigenvalues of -Kg relative to K; see the module
            # docstring for why the problem is posed this way round.
            values, vectors = spla.eigsh(
                -geometric, k=wanted, M=stiffness, which="LA"
            )
        except (spla.ArpackNoConvergence, RuntimeError, ValueError) as exc:
            raise SolverError(
                f"The buckling eigensolver did not converge ({exc}). The mesh may be too "
                "coarse for the mode, or the model may need more constraint."
            ) from exc

        factors: list[float] = []
        keep: list[int] = []
        for index, value in enumerate(values):
            if abs(value) < _MIN_FACTOR:
                continue
            factors.append(float(1.0 / value))
            keep.append(index)

        order = sorted(range(len(factors)), key=lambda i: (factors[i] <= 0.0, abs(factors[i])))
        shapes = np.zeros((len(order), mesh.node_count, 3), dtype=np.float64)
        for slot, index in enumerate(order):
            full = np.zeros(n_dof, dtype=np.float64)
            full[free] = vectors[:, keep[index]]
            shapes[slot] = full.reshape(-1, 3)
        return [factors[i] for i in order], shapes


def euler_critical_load_n(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    material: Material,
    end_condition: str = "clamped-free",
) -> float:
    """Closed-form Euler buckling load, `P = pi^2 E I / (K L)^2`.

    Here so the tests have something exact to compare with. `K` is the effective
    length factor: 2 for clamped-free (a cantilever column), 1 for pinned-pinned,
    0.5 for clamped-clamped. `I` is the *smaller* second moment, because a column
    buckles about its weak axis.
    """
    factors = {"clamped-free": 2.0, "pinned-pinned": 1.0, "clamped-clamped": 0.5}
    if end_condition not in factors:
        raise ValueError(f"end_condition must be one of {sorted(factors)}")
    inertia = min(
        width_mm * height_mm**3 / 12.0,
        height_mm * width_mm**3 / 12.0,
    )
    effective = factors[end_condition] * length_mm
    return float(np.pi**2 * material.youngs_modulus_mpa * inertia / effective**2)
