"""Turning solved fields into what a caller reads.

Shared by every `Solver`, and shared deliberately: two solvers that summarised
their own results would be free to mean different things by "factor of safety",
and master plan 6.5 keeps the hand-written solver as an **oracle** for CalculiX —
a comparison that is only meaningful if both sides computed the summary the same
way. A disagreement should localise to the physics, never to the reporting.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from app.mesh.structural import ShellMesh
from app.mesh.types import TetMesh
from app.solve.types import LoadCase, StaticResult


def nodal_average(mesh: TetMesh, element_values: NDArray[np.float64]) -> NDArray[np.float64]:
    """Spread constant-per-element values onto nodes by simple averaging.

    Element stress is discontinuous across element faces; averaging at the nodes
    is the standard smoothing that makes a contour plot readable. It is a
    display convenience only -- the reported peak stress stays the raw element
    value, so a stress concentration is never smoothed away from the headline
    number.

    Midside nodes are included for a tet10 mesh, so every node the viewer might
    address carries a value rather than a hole.

    Works on a scalar field (n_elements,) and on a tensor field (n_elements, k)
    alike, mirroring `element_average` — the two are inverses and an asymmetry
    between them is how a caller ends up transposing a stress tensor. A benchmark
    that reads one stress component at a point needs the tensor form: von Mises
    is unsigned and cannot stand in for `sigma_yy` on a plate in bending, where
    the two faces carry equal and opposite stress.
    """
    values = np.asarray(element_values, dtype=np.float64)
    connectivity = mesh.connectivity
    shape = (mesh.node_count,) if values.ndim == 1 else (mesh.node_count, values.shape[1])
    totals = np.zeros(shape, dtype=np.float64)
    counts = np.zeros(mesh.node_count, dtype=np.int64)
    # `values[:, None]` broadcasts one element's value across its nodes; for a
    # tensor that has to become `values[:, None, :]`, which is the same
    # statement with the component axis kept on the end.
    np.add.at(totals, connectivity, values[:, None] if values.ndim == 1 else values[:, None, :])
    np.add.at(counts, connectivity, 1)
    divisor = counts if values.ndim == 1 else counts[:, None]
    return np.divide(totals, divisor, out=np.zeros_like(totals), where=divisor > 0)


def element_average(mesh: TetMesh, nodal_values: NDArray[np.float64]) -> NDArray[np.float64]:
    """The inverse of `nodal_average`: a nodal field read back per element.

    Needed by any solver that reports at nodes while `SolveOutput` carries stress
    per element — CalculiX writes its `.frd` that way. The average is over the
    four **corner** nodes only, even on a tet10 mesh: a midside node sits on an
    edge shared by more elements than a corner is, so including it weights the
    element's own value towards its neighbours. Corner-only is what makes a
    uniform field come back exactly uniform, which is what an oracle comparison
    against a closed-form uniform stress state rests on.

    Works on a scalar field (n_nodes,) and on a tensor field (n_nodes, k) alike;
    the average is taken over the node axis in both cases.
    """
    corners = mesh.tets[:, :4]
    return np.asarray(nodal_values)[corners].mean(axis=1)


def shell_element_average(
    mesh: ShellMesh, nodal_values: NDArray[np.float64]
) -> NDArray[np.float64]:
    """`element_average` for a shell: a nodal field read back per face.

    **Corner nodes only, for exactly the reason the solid version gives** — a
    midside node sits on an edge shared by more faces than a corner is, so
    including it drags a face's own value towards its neighbours, and a uniform
    field would no longer come back exactly uniform. That last property is what
    an oracle comparison against a closed-form uniform stress state rests on, so
    it is the property to preserve rather than the averaging that is convenient.

    A separate function rather than a widened `element_average`, because the two
    differ in the one line that matters — `mesh.tets[:, :4]` against
    `mesh.faces` — and a single function branching on the mesh type would put a
    silent choice between two averaging rules inside a call that reads like one.
    """
    return np.asarray(nodal_values)[mesh.faces].mean(axis=1)


def summarise_static(
    mesh: TetMesh,
    case: LoadCase,
    displacements: NDArray[np.float64],
    von_mises_per_element: NDArray[np.float64],
    warnings: list[str],
    seconds: float,
) -> StaticResult:
    """The summary of one linear static run, however it was solved.

    The peak stress is the raw per-element value and is never the smoothed one:
    a factor of safety read off a smoothed field is optimistic exactly where it
    matters, at the concentration. `nodal_average` above exists for display and
    says the same thing from the other side.
    """
    return _summarise(
        case,
        displacements,
        von_mises_per_element,
        warnings,
        seconds,
        volume_mm3=mesh.volume,
        node_count=mesh.node_count,
        element_count=mesh.tet_count,
    )


def summarise_shell_static(
    mesh: ShellMesh,
    case: LoadCase,
    thickness_mm: float,
    displacements: NDArray[np.float64],
    von_mises_per_element: NDArray[np.float64],
    warnings: list[str],
    seconds: float,
) -> StaticResult:
    """`summarise_static` for a shell, which differs only in what it is made of.

    A shell mesh has no volume of its own — `volume_mm3` is area times the
    thickness the mesh deliberately does not carry, which is what makes
    `mass_kg` mean the same thing here as it does for a solid. It is taken as an
    argument rather than read off a section object so this stays a function of
    the numbers it reports.

    **The two share `_summarise` rather than each building a `StaticResult`.**
    This module's whole argument is that two solvers summarising separately are
    free to drift on what "factor of safety" means, and 6.5's oracle comparison
    is only meaningful if both sides computed the summary identically. A shell
    twin that reimplemented the peak-and-divide would be that drift, arriving by
    the door this module exists to hold shut.
    """
    return _summarise(
        case,
        displacements,
        von_mises_per_element,
        warnings,
        seconds,
        volume_mm3=mesh.volume_mm3(thickness_mm),
        node_count=mesh.node_count,
        element_count=mesh.face_count,
    )


def _summarise(
    case: LoadCase,
    displacements: NDArray[np.float64],
    von_mises_per_element: NDArray[np.float64],
    warnings: list[str],
    seconds: float,
    *,
    volume_mm3: float,
    node_count: int,
    element_count: int,
) -> StaticResult:
    """The one definition of what a static result means. See both callers."""
    magnitudes = np.linalg.norm(displacements.reshape(-1, 3), axis=1)
    peak_node = int(np.argmax(magnitudes))
    peak_element = int(np.argmax(von_mises_per_element))
    peak_stress = float(von_mises_per_element[peak_element])

    yield_strength = case.material.yield_strength_mpa
    fos = yield_strength / peak_stress if peak_stress > 0.0 else float("inf")

    return StaticResult(
        max_displacement_mm=float(magnitudes[peak_node]),
        max_displacement_node=peak_node,
        max_von_mises_mpa=peak_stress,
        max_von_mises_element=peak_element,
        factor_of_safety=fos,
        yields=peak_stress >= yield_strength,
        mass_kg=volume_mm3 * 1e-9 * case.material.density_kg_m3,
        volume_mm3=volume_mm3,
        node_count=node_count,
        element_count=element_count,
        solve_seconds=seconds,
        warnings=warnings,
    )


__all__ = [
    "element_average",
    "nodal_average",
    "shell_element_average",
    "summarise_shell_static",
    "summarise_static",
]
