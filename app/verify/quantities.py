"""The quantity a verification study is *about*.

A convergence study and a benchmark both need the same thing: one scalar, read
out of a solved model the same way at every refinement level, with a name and a
unit attached. That is all a `Quantity` is.

**A reader takes the mesh as well as the solver output**, deliberately. The
interesting quantities in a benchmark are read *at a point* — "the hoop
displacement at the outer surface", "the stress at point D" — and a point only
means something against nodes. Passing only the output would force every
point-wise reader to close over a mesh it was not given, which is how a level of
a convergence study ends up reading the previous level's mesh.

**Nothing here interpolates.** `displacement_component_at` reports the value at
the *nearest node* and `von_mises_near` at the *nearest element centroid*, and
both are honest about it: on a coarse grid the nearest node may be some way from
the point asked for. That is not a defect to paper over with interpolation — it
is discretisation error, which is exactly what the convergence study measures.
Interpolating would smuggle a smoothing step in between the solver and the
quantity being converged, and the study would then be measuring the smoother.

Units are mm-N-MPa, like everything else here. Nothing converts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.mesh.types import TetMesh

#: How a scalar is read out of one solved model. The second argument is whatever
#: the analysis produced — `SolveOutput`, `ModalOutput`, or a solver's own result
#: object — because the four analyses do not share an output type and forcing
#: them to would be the union-typed `solve()` that `app/solve/base.py` refused.
Reader = Callable[[TetMesh, Any], float]

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


@dataclass(frozen=True)
class Quantity:
    """A named scalar read out of a solved model, with its unit.

    `unit` is a plain string and is never parsed or converted — it exists so a
    report, a register entry and a benchmark target can be compared by a human
    and by a test, and so a target quoted in MPa can never be silently compared
    against a value read in mm.
    """

    name: str
    unit: str
    read: Reader

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("A quantity must have a name; a nameless number is not a result.")
        if not self.unit.strip():
            raise ValueError(
                f"Quantity {self.name!r} has no unit. Every number in this codebase is "
                "mm-N-MPa and says which; an unlabelled one cannot be checked against a "
                "benchmark target."
            )


def _nearest_node(mesh: TetMesh, point_mm: tuple[float, float, float]) -> int:
    target = np.asarray(point_mm, dtype=np.float64)
    return int(np.argmin(np.linalg.norm(mesh.nodes - target, axis=1)))


def _nearest_element(mesh: TetMesh, point_mm: tuple[float, float, float]) -> int:
    target = np.asarray(point_mm, dtype=np.float64)
    centroids = mesh.nodes[mesh.tets].mean(axis=1)
    return int(np.argmin(np.linalg.norm(centroids - target, axis=1)))


def displacement_component_at(
    point_mm: tuple[float, float, float], axis: str, *, name: str | None = None
) -> Quantity:
    """One displacement component at the node nearest a point, in mm.

    Signed, not magnitude: a benchmark target for a deflection has a direction,
    and comparing a magnitude against it would pass a part that moved the wrong
    way.
    """
    if axis not in _AXIS_INDEX:
        raise ValueError(f"axis must be one of x, y, z — got {axis!r}")
    index = _AXIS_INDEX[axis]

    def read(mesh: TetMesh, output: Any) -> float:
        node = _nearest_node(mesh, point_mm)
        return float(np.asarray(output.displacements).reshape(-1, 3)[node, index])

    label = name or f"u{axis} at {point_mm}"
    return Quantity(name=label, unit="mm", read=read)


def von_mises_near(point_mm: tuple[float, float, float], *, name: str | None = None) -> Quantity:
    """Von Mises stress in the element whose centroid is nearest a point, in MPa.

    Raw element stress, never the nodal-averaged field: `app.solve.postprocess`
    keeps that distinction for the same reason — a peak read off a smoothed
    field is optimistic exactly at the concentration.
    """

    def read(mesh: TetMesh, output: Any) -> float:
        element = _nearest_element(mesh, point_mm)
        return float(np.asarray(output.von_mises)[element])

    label = name or f"von Mises near {point_mm}"
    return Quantity(name=label, unit="MPa", read=read)


def _max_displacement(mesh: TetMesh, output: Any) -> float:
    return float(output.result.max_displacement_mm)


def _max_von_mises(mesh: TetMesh, output: Any) -> float:
    return float(output.result.max_von_mises_mpa)


#: Peak displacement magnitude over the whole part.
#:
#: A poor thing to converge on a part with a singularity in it — a re-entrant
#: corner has no finite peak stress, so the peak *never* stops moving and the
#: study correctly refuses to state a value. That refusal is the right answer,
#: not a reason to prefer a smoothed quantity.
MAX_DISPLACEMENT = Quantity(name="max displacement", unit="mm", read=_max_displacement)

#: Peak von Mises stress over the whole part. See the caveat on MAX_DISPLACEMENT.
MAX_VON_MISES = Quantity(name="max von Mises", unit="MPa", read=_max_von_mises)


def modal_frequency(index: int, *, name: str | None = None) -> Quantity:
    """The `index`-th natural frequency, zero-based, in Hz.

    Zero-based over the *whole* list including rigid-body modes, because which
    modes are rigid is a property of the answer rather than of the question: a
    reader that silently skipped them would report a different mode on a grid
    where one rigid mode came back at 1e-3 Hz instead of 1e-9.
    """
    if index < 0:
        raise ValueError("mode index is zero-based and cannot be negative")

    def read(mesh: TetMesh, output: Any) -> float:
        frequencies = np.asarray(output.frequencies_hz, dtype=np.float64)
        if index >= len(frequencies):
            raise IndexError(
                f"Mode {index} was asked for but only {len(frequencies)} were computed. "
                f"Raise `modes` on the ModalCase to at least {index + 1}."
            )
        return float(frequencies[index])

    return Quantity(name=name or f"mode {index} frequency", unit="Hz", read=read)


def _critical_load_factor(mesh: TetMesh, output: Any) -> float:
    factor = output.critical_load_factor
    if factor is None:
        raise ValueError(
            "No positive buckling load factor was found, so there is nothing to "
            "converge. The structure does not buckle under this load in this "
            "direction; reverse the load and ask again."
        )
    return float(factor)


#: The multiplier on the applied loads at which the structure buckles.
CRITICAL_LOAD_FACTOR = Quantity(
    name="critical load factor", unit="x applied load", read=_critical_load_factor
)


__all__ = [
    "CRITICAL_LOAD_FACTOR",
    "MAX_DISPLACEMENT",
    "MAX_VON_MISES",
    "Quantity",
    "Reader",
    "displacement_component_at",
    "modal_frequency",
    "von_mises_near",
]
