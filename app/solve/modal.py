"""Natural frequencies and mode shapes.

Solves the generalised eigenproblem `K phi = omega^2 M phi` on the same tet4 or
tet10 mesh the static solver uses, reusing its stiffness assembly and its
fixture-to-DOF machinery so the two cannot drift apart on what "clamped" means.

**Units.** The rest of this codebase is mm-N-MPa and nothing converts, so the
mass matrix has to arrive in that system rather than drag SI into it. In
mm-N-second the consistent mass unit is the tonne, so a density quoted in
kg/m^3 -- which is what `Material` carries and what a datasheet prints -- is
multiplied by 1e-12 exactly once, at the boundary, in `_density_tonne_mm3`.
Getting this wrong is not subtle: a factor of 1e12 in M puts every frequency
out by 1e6, so the closed-form tests below would fail by six orders of
magnitude rather than quietly drift.

**The mass matrix is integrated exactly, in closed form.** For a straight-sided
tetrahedron the Jacobian is constant, so the integral of a product of shape
functions reduces to the standard barycentric monomial identity

    int_T L1^a L2^b L3^c L4^d dV = 6V a! b! c! d! / (a+b+c+d+3)!

and no quadrature rule is involved. That matters for tet10: its shape functions
are quadratic, so `N^T N` is quartic, and the four-point rule the stiffness
assembly uses is exact only to degree two. Reusing it here would have produced a
mass matrix that is wrong by a few percent -- small enough to look plausible and
to survive a smoke test, which is exactly the kind of error that reaches a
report. The straight-sided assumption is not an assumption in this codebase: the
mesher sets `Mesh.SecondOrderLinear` and `_assert_midside_ordering` checks every
midside node really is at its edge's midpoint.
"""

from __future__ import annotations

import time
from math import factorial, pi, sqrt

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray

from app.mesh.types import TET10_EDGES, TetMesh
from app.solve.base import ModalOutput, ModalSolver
from app.solve.linear_static import _dof_indices, assemble_stiffness
from app.solve.selection import select_nodes
from app.solve.types import Material, ModalCase, ModalResult, SolverError

#: A frequency below this is a rigid-body mode, not a structural one.
#:
#: Rigid-body modes are zero in exact arithmetic and come back as small
#: non-zero values from a shift-inverted eigensolver -- typically 1e-4 Hz where
#: the first elastic mode of anything worth meshing is hundreds. Anything in
#: between would be a mode the solver has not resolved, so the gap is wide on
#: purpose rather than tight.
RIGID_BODY_HZ = 1.0

#: Where to put the shift for shift-invert, and how negative an eigenvalue may
#: be before it is an error -- both as a fraction of the problem's own scale.
#:
#: Neither can be an absolute number. Eigenvalues here are omega^2 in rad^2/s^2,
#: which for a steel bracket runs to 1e10 and for a rubber grommet to 1e4, so a
#: fixed tolerance of "1.0" is simultaneously far too tight for one and far too
#: loose for the other. A rigid-body eigenvalue is zero in exact arithmetic and
#: comes back from the factorisation with round-off proportional to the norm of
#: the matrices, so the only meaningful test is relative.
_SHIFT_FRACTION = -1e-6
_NEGATIVE_TOLERANCE = -1e-6


def _density_tonne_mm3(material: Material) -> float:
    """Density in the mm-N-s mass unit. See the units note in the module docstring."""
    return float(material.density_kg_m3) * 1e-12


def _shape_polynomials(order: int) -> list[dict[tuple[int, int, int, int], float]]:
    """Each shape function as barycentric monomials: exponents -> coefficient.

    Tet4 is `N_i = L_i`. Tet10 is `N_i = L_i(2L_i - 1)` at the corners and
    `N_ab = 4 L_a L_b` at the midside of edge (a, b), in `TET10_EDGES` order --
    the same ordering the stiffness assembly and the mesher both use.
    """

    def monomial(*exponents: int) -> tuple[int, int, int, int]:
        return (exponents[0], exponents[1], exponents[2], exponents[3])

    if order == 1:
        return [{monomial(*(1 if j == i else 0 for j in range(4))): 1.0} for i in range(4)]

    polynomials: list[dict[tuple[int, int, int, int], float]] = []
    for corner in range(4):
        squared = monomial(*(2 if j == corner else 0 for j in range(4)))
        linear = monomial(*(1 if j == corner else 0 for j in range(4)))
        polynomials.append({squared: 2.0, linear: -1.0})
    for a, b in TET10_EDGES:
        exponents = [0, 0, 0, 0]
        exponents[a] += 1
        exponents[b] += 1
        polynomials.append({monomial(*exponents): 4.0})
    return polynomials


def _unit_mass_matrix(order: int) -> NDArray[np.float64]:
    """`int N_i N_j dV` over a tet of unit volume, shape (nodes, nodes).

    Volume-independent, so it is computed once per order and scaled per element.
    """
    polynomials = _shape_polynomials(order)
    size = len(polynomials)
    matrix = np.zeros((size, size), dtype=np.float64)
    for i, first in enumerate(polynomials):
        for j, second in enumerate(polynomials):
            total = 0.0
            for exponents_i, coeff_i in first.items():
                for exponents_j, coeff_j in second.items():
                    powers = [a + b for a, b in zip(exponents_i, exponents_j)]
                    numerator = 1
                    for power in powers:
                        numerator *= factorial(power)
                    # 6V * a!b!c!d!/(sum+3)!, with V = 1 here.
                    total += coeff_i * coeff_j * 6.0 * numerator / factorial(sum(powers) + 3)
            matrix[i, j] = total
    return matrix


def assemble_mass(mesh: TetMesh, material: Material) -> sp.csr_matrix:
    """Consistent mass matrix, in tonnes, for whichever element order the mesh carries.

    Consistent rather than lumped. A lumped (diagonal) matrix is cheaper and is
    what an explicit dynamics code wants, but it systematically under-predicts
    frequencies for the bending modes that matter most on the thin-walled parts
    this platform is for, and the eigensolver here is implicit anyway.
    """
    density = _density_tonne_mm3(material)
    if density <= 0.0:
        raise SolverError(
            f"{material.name!r} has no density, so it has no mass and no natural "
            "frequencies. Give the material a density_kg_m3 above zero."
        )

    connectivity = mesh.connectivity
    # A method here, not a property -- unlike `mesh.volume` next to it.
    volumes = np.abs(mesh.signed_volumes())
    unit = _unit_mass_matrix(mesh.element_order)

    # One scalar block per element, expanded to three identical DOF blocks: mass
    # couples x with x, never x with y.
    scalar = density * volumes[:, None, None] * unit[None, :, :]
    nodes_per_element = connectivity.shape[1]
    block = np.zeros(
        (len(connectivity), 3 * nodes_per_element, 3 * nodes_per_element), dtype=np.float64
    )
    for axis in range(3):
        block[:, axis::3, axis::3] = scalar

    dofs = _dof_indices(connectivity.ravel()).reshape(len(connectivity), -1)
    rows = np.repeat(dofs, 3 * nodes_per_element, axis=1).ravel()
    cols = np.tile(dofs, (1, 3 * nodes_per_element)).ravel()
    n_dof = 3 * mesh.node_count
    return sp.coo_matrix(
        (block.ravel(), (rows, cols)), shape=(n_dof, n_dof), dtype=np.float64
    ).tocsr()


class ModalEigenSolver(ModalSolver):
    name = "modal-lanczos"

    def solve(self, mesh: TetMesh, case: ModalCase) -> ModalOutput:
        started = time.perf_counter()
        warnings: list[str] = []

        n_dof = 3 * mesh.node_count
        if case.fixtures:
            fixed = np.unique(
                np.concatenate(
                    [
                        _dof_indices(select_nodes(mesh, fixture.where), fixture.held)
                        for fixture in case.fixtures
                    ]
                )
            )
        else:
            fixed = np.empty(0, dtype=np.int64)
        free = np.setdiff1d(np.arange(n_dof), fixed)
        if len(free) == 0:
            raise SolverError("Every degree of freedom is fixed; there is nothing to vibrate")

        stiffness = assemble_stiffness(mesh, case.material)[free][:, free].tocsc()
        mass = assemble_mass(mesh, case.material)[free][:, free].tocsc()

        wanted = min(case.modes, len(free) - 2)
        if wanted < 1:
            raise SolverError(
                "The model has too few free degrees of freedom to extract a mode. "
                "Refine the mesh or release a fixture."
            )
        if wanted < case.modes:
            warnings.append(
                f"Asked for {case.modes} modes; this mesh only supports {wanted}."
            )

        eigenvalues = self._eigenvalues(stiffness, mass, wanted)
        # Numerical noise puts a rigid-body eigenvalue a little either side of
        # zero. Clipping is right for that and wrong for anything larger, so
        # "larger" is measured against the spectrum this problem actually has
        # rather than against a constant -- see `_NEGATIVE_TOLERANCE`.
        scale = float(np.max(np.abs(eigenvalues))) or 1.0
        if np.any(eigenvalues < _NEGATIVE_TOLERANCE * scale):
            raise SolverError(
                "The eigensolver returned a negative eigenvalue, which means the "
                "stiffness matrix is not positive semi-definite. The mesh is "
                "probably degenerate."
            )
        eigenvalues = np.clip(eigenvalues, 0.0, None)

        frequencies = np.sqrt(eigenvalues) / (2.0 * pi)
        order = np.argsort(frequencies)
        frequencies = frequencies[order]

        shapes = np.zeros((len(frequencies), mesh.node_count, 3), dtype=np.float64)
        vectors = self._vectors
        if vectors is not None:
            for index, column in enumerate(order):
                full = np.zeros(n_dof, dtype=np.float64)
                full[free] = vectors[:, column]
                shapes[index] = full.reshape(-1, 3)

        rigid = int(np.count_nonzero(frequencies < RIGID_BODY_HZ))
        if not case.fixtures and rigid < 6 and len(frequencies) >= 6:
            warnings.append(
                f"A free-free model has six rigid-body modes; only {rigid} came back "
                "below the threshold, so the lowest elastic frequency may be wrong."
            )

        volume = float(mesh.volume)
        result = ModalResult(
            frequencies_hz=[float(value) for value in frequencies],
            rigid_body_modes=rigid,
            mass_kg=volume * 1e-9 * float(case.material.density_kg_m3),
            volume_mm3=volume,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            solve_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        return ModalOutput(result=result, frequencies_hz=frequencies, shapes=shapes)

    def _eigenvalues(
        self, stiffness: sp.csc_matrix, mass: sp.csc_matrix, wanted: int
    ) -> NDArray[np.float64]:
        """The `wanted` smallest eigenvalues of `K phi = lambda M phi`.

        Shift-invert, because the modes an engineer cares about are the lowest
        and Lanczos converges from the outside in: asked for the smallest
        directly (`which="SM"`) it iterates for a very long time and often
        returns junk. `sigma` turns "smallest" into "largest of the inverse",
        which converges quickly.
        """
        self._vectors = None
        # The shift has the units of omega^2, so it is derived from the matrices
        # rather than hard-coded: the mean of K's diagonal over M's diagonal is
        # a cheap estimate of the problem's frequency scale. It must be non-zero
        # because a free-free K is singular at exactly zero, which is the one
        # case where the factorisation inside shift-invert would fail.
        reference = float(stiffness.diagonal().mean() / mass.diagonal().mean())
        shift = _SHIFT_FRACTION * reference
        try:
            values, vectors = spla.eigsh(
                stiffness, k=wanted, M=mass, sigma=shift, which="LM"
            )
        except (spla.ArpackNoConvergence, RuntimeError, ValueError) as exc:
            raise SolverError(
                f"The eigensolver did not converge ({exc}). The mesh may be degenerate, "
                "or the model may need more constraint."
            ) from exc
        self._vectors = vectors
        return np.asarray(values, dtype=np.float64)


def beam_bending_hz(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    material: Material,
    mode: int = 1,
) -> float:
    """Closed-form first bending frequency of a clamped-free rectangular beam.

    Euler-Bernoulli: `f = (beta L)^2 / (2 pi L^2) * sqrt(E I / (rho A))`. Here so
    the tests have something exact to check against and so a reader can see what
    "correct" means for the numbers the solver produces; it is not used by the
    solver itself.
    """
    betas = (1.875104068711961, 4.694091132974175, 7.854757438237613)
    if not 1 <= mode <= len(betas):
        raise ValueError(f"mode must be 1..{len(betas)}")
    inertia = width_mm * height_mm**3 / 12.0
    area = width_mm * height_mm
    density = _density_tonne_mm3(material)
    return (betas[mode - 1] ** 2 / (2.0 * pi * length_mm**2)) * sqrt(
        material.youngs_modulus_mpa * inertia / (density * area)
    )


def bar_axial_hz(length_mm: float, material: Material, mode: int = 1) -> float:
    """Closed-form axial frequency of a clamped-free bar: `f = (2n-1)/(4L) sqrt(E/rho)`.

    Independent of cross-section, which makes it the cleaner of the two checks:
    a wrong area or second moment cannot hide in it, so it isolates the mass
    matrix and the unit conversion.
    """
    return (2 * mode - 1) / (4.0 * length_mm) * sqrt(
        material.youngs_modulus_mpa / _density_tonne_mm3(material)
    )
