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

**A uniform change or a field, and the difference is where the number comes
from rather than what is done with it.** Every function here takes `delta_t_k`
as either one float -- the whole part heated by the same amount -- or one value
per element, and the arithmetic is identical either way: thermal strain is a
local quantity and always was. What a field needs beyond this is a *source* for
it, and there are two, which are different things:

- **prescribed**, where the benchmark or the engineer states the field as a
  formula of position. NAFEMS LE11 is this: its temperature is
  `sqrt(x^2 + y^2) + z`, given, not computed. This is what the per-element form
  below serves.
- **solved**, where a conduction analysis computes it from boundary conditions.
  That is `app/solve/conduction.py`, a different analysis with different inputs.

Neither is invented here. A caller with no field passes one number and gets the
uniform case it always had.
"""

from __future__ import annotations

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


#: A temperature change: one number for the whole part, or one per element.
Temperature = float | NDArray[np.float64]


def thermal_strain(material: Material, delta_t_k: Temperature) -> NDArray[np.float64]:
    """`alpha * dT` in Voigt form: shape (6,) for a uniform change, (n, 6) for a field."""
    alpha = material.thermal_expansion_per_k
    if alpha is None:
        raise SolverError(
            f"{material.name!r} has no coefficient of thermal expansion, so its thermal "
            "stress cannot be computed. Set thermal_expansion_per_k on the material "
            "(per kelvin -- 23.6e-6 for aluminium)."
        )
    if np.isscalar(delta_t_k):
        return float(alpha) * float(delta_t_k) * _DILATATION  # type: ignore[arg-type]
    field = np.asarray(delta_t_k, dtype=np.float64).reshape(-1)
    return float(alpha) * field[:, None] * _DILATATION[None, :]


def thermal_load(
    mesh: TetMesh, material: Material, delta_t_k: Temperature
) -> NDArray[np.float64]:
    """Equivalent nodal forces for a temperature change, shape (3 * n_nodes,).

    Integrated the same way the stiffness is, so the two agree element by
    element: one evaluation for tet4, whose strain is constant, and the
    four-point rule for tet10.

    `delta_t_k` may be one number or one value per element. The per-element form
    is *not* an approximation of a field: within an element the strain is taken
    as constant, which is exactly what the uniform case does, so the two paths
    are the same integration and only the operand's shape differs.
    """
    strain = thermal_strain(material, delta_t_k)
    d = constitutive_matrix(material)
    # The stress a fully restrained element would carry: (6,) uniform, (n, 6)
    # for a field. `einsum` below is written for the per-element shape, so a
    # uniform change is broadcast up rather than branched on.
    stress = np.atleast_2d(strain @ d.T)
    if stress.shape[0] == 1:
        stress = np.repeat(stress, len(mesh.connectivity), axis=0)

    connectivity = mesh.connectivity
    element_dofs = _element_dofs(connectivity)
    forces = np.zeros(3 * mesh.node_count, dtype=np.float64)

    if mesh.midside is None:
        grads, volumes = _shape_gradients(mesh)
        b = _strain_displacement(grads)
        local = volumes[:, None] * np.einsum("eij,ei->ej", b, stress)
    else:
        points = mesh.nodes[connectivity]
        local = np.zeros((len(connectivity), 3 * connectivity.shape[1]), dtype=np.float64)
        for point in _TET_GAUSS_POINTS:
            grads, detj = _mapped_gradients(points, _tet10_shape_gradients(*point))
            b = _strain_displacement(grads)
            local += _TET_GAUSS_WEIGHT * detj[:, None] * np.einsum("eij,ei->ej", b, stress)

    np.add.at(forces, element_dofs.ravel(), local.ravel())
    return forces


def thermal_stress_correction(
    material: Material, delta_t_k: Temperature
) -> NDArray[np.float64]:
    """`D * epsilon_thermal`, the stress to subtract during recovery.

    Shape (6,) for a uniform change and (n, 6) for a field, which is what the
    recovery step subtracts from either way — `stress - correction` broadcasts
    when the correction is uniform and lines up element-by-element when it is
    not.

    A separate function because forgetting it is the classic thermal-stress bug
    and a named thing is harder to forget than a term in an expression.
    """
    return thermal_strain(material, delta_t_k) @ constitutive_matrix(material).T


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
