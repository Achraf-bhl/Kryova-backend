"""Thermal stress from a uniform temperature change.

A part that is heated wants to grow. Where something stops it growing, the
restrained expansion shows up as stress -- and that stress can be large: 100 K
on restrained steel is about 280 MPa, which is most of mild steel's yield with
no mechanical load at all.

This solves the restrained case: a uniform `delta_t_k` over the whole part,
applied as an equivalent nodal load

    f_thermal = integral B^T D epsilon_thermal dV,   epsilon_thermal = alpha dT [1,1,1,0,0,0]

and then recovers stress as `D (B u - epsilon_thermal)`. **Subtracting the
thermal strain in the recovery step is the part that is easy to leave out**, and
leaving it out is not a small error: it reports the stress of a part that
expanded freely, which for a fully restrained bar is exactly the wrong sign and
the wrong magnitude. The test suite pins the restrained-bar case against the
closed form `sigma = -E alpha dT` for that reason.

**Uniform temperature only.** A real thermal problem has a temperature *field*,
which needs a conduction solve with its own boundary conditions -- a different
analysis with different inputs. `ThermalCase` therefore takes one number and
says so, rather than accepting a field it would have to invent.
"""

from __future__ import annotations

import time

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.linear_static import (
    _TET_GAUSS_POINTS,
    _TET_GAUSS_WEIGHT,
    _element_dofs,
    _mapped_gradients,
    _shape_gradients,
    _strain_displacement,
    _tet10_shape_gradients,
    constitutive_matrix,
)
from app.solve.types import Material, SolverError

#: Thermal strain is dilatational: it stretches, it does not shear. In Voigt
#: order [xx, yy, zz, xy, yz, zx] that is ones on the three normal components
#: and zeros on the three shears.
_DILATATION = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float64)


def thermal_strain(material: Material, delta_t_k: float) -> NDArray[np.float64]:
    """`alpha * dT` in Voigt form, shape (6,)."""
    alpha = material.thermal_expansion_per_k
    if alpha is None:
        raise SolverError(
            f"{material.name!r} has no coefficient of thermal expansion, so its thermal "
            "stress cannot be computed. Set thermal_expansion_per_k on the material "
            "(per kelvin -- 23.6e-6 for aluminium)."
        )
    return float(alpha) * float(delta_t_k) * _DILATATION


def thermal_load(
    mesh: TetMesh, material: Material, delta_t_k: float
) -> NDArray[np.float64]:
    """Equivalent nodal forces for a uniform temperature change, shape (3 * n_nodes,).

    Integrated the same way the stiffness is, so the two agree element by
    element: one evaluation for tet4, whose strain is constant, and the
    four-point rule for tet10.
    """
    strain = thermal_strain(material, delta_t_k)
    d = constitutive_matrix(material)
    stress = d @ strain  # the stress a fully restrained element would carry

    connectivity = mesh.connectivity
    element_dofs = _element_dofs(connectivity)
    forces = np.zeros(3 * mesh.node_count, dtype=np.float64)

    if mesh.midside is None:
        grads, volumes = _shape_gradients(mesh)
        b = _strain_displacement(grads)
        local = volumes[:, None] * np.einsum("eij,i->ej", b, stress)
    else:
        points = mesh.nodes[connectivity]
        local = np.zeros((len(connectivity), 3 * connectivity.shape[1]), dtype=np.float64)
        for point in _TET_GAUSS_POINTS:
            grads, detj = _mapped_gradients(points, _tet10_shape_gradients(*point))
            b = _strain_displacement(grads)
            local += _TET_GAUSS_WEIGHT * detj[:, None] * np.einsum("eij,i->ej", b, stress)

    np.add.at(forces, element_dofs.ravel(), local.ravel())
    return forces


def thermal_stress_correction(material: Material, delta_t_k: float) -> NDArray[np.float64]:
    """`D * epsilon_thermal`, the stress to subtract during recovery, shape (6,).

    A separate function because forgetting it is the classic thermal-stress bug
    and a named thing is harder to forget than a term in an expression.
    """
    return constitutive_matrix(material) @ thermal_strain(material, delta_t_k)


def restrained_bar_stress_mpa(material: Material, delta_t_k: float) -> float:
    """Closed form for a bar restrained along one axis and free on the others:
    `sigma = -E alpha dT`.

    Compression for a temperature rise, which is why the sign is negative. Used
    by the tests, and here rather than in them so the expected physics is stated
    next to the implementation it checks.
    """
    alpha = material.thermal_expansion_per_k
    if alpha is None:
        raise SolverError(f"{material.name!r} has no coefficient of thermal expansion")
    return -material.youngs_modulus_mpa * float(alpha) * float(delta_t_k)
