"""`ShellSolver` — the seam THE QUEUE's A6 and E7 task 1 both name.

`deck.py` writes a shell deck, `run.py` runs it, `frd.py` reads it, and
`shell_loads.py` produces the force vector. Nothing joined them, so a shell
could be meshed, loaded and written and still not *solved*: the four pieces were
in the box and the box had no lid. This is the lid.

**It is not a `Solver`, and that is the decision rather than an omission.** The
ABC in `app/solve/base.py` is `solve(mesh: TetMesh, case: LoadCase)`, and a
shell needs a third thing neither argument carries — its `ShellSection`, because
`app/mesh/structural.py` keeps thickness off the mesh so that one mesh can be
solved at several thicknesses. Widening the ABC to `TetMesh | ShellMesh` and an
optional section would put an optional argument on every solver in the codebase
that is mandatory for exactly one of them, and make "which meshes does this
solver take?" unanswerable at the type level. `app/solve/plane.py` already
declined the same widening for the same reason and `PlaneSolver` sits beside its
own case type; this follows it. The cost is real and is stated: **a caller must
know it has a shell**, and the job layer cannot yet dispatch to this
automatically.

Everything downstream is shared rather than re-implemented — `run_ccx`,
`diagnose`, `parse_frd`, `displacements` and `nodal_stress_tensor` are all
mesh-agnostic already, and the summary goes through `_summarise` so a shell and
a solid cannot drift on what a factor of safety means.

**`OUTPUT=2D` is what makes the reader work here at all**, and it was measured
rather than assumed: ccx expands a shell into solids and writes the `.frd` for
the *expanded* model unless asked otherwise, in which case
`displacements(frd, mesh.node_count)` would index another model's answer with
every index in range and no length mismatch. `write_frame_deck` asks for it, and
the seat confirmed on 2026-09-09 that ccx 2.23 honours it on both keywords at
the submitted node count.
"""

from __future__ import annotations

import os
import time

import numpy as np
from numpy.typing import NDArray

from app.mesh.structural import ShellMesh
from app.solve.base import SolveOutput
from app.solve.calculix.deck import write_frame_deck
from app.solve.calculix.diagnose import diagnose
from app.solve.calculix.frd import (
    displacements,
    nodal_stress_tensor,
    parse_frd,
    von_mises_from_tensor,
)
from app.solve.calculix.run import DEFAULT_TIMEOUT_S, run_ccx
from app.solve.calculix.solver import _warnings_from, _with_evidence
from app.solve.postprocess import shell_element_average, summarise_shell_static
from app.solve.sections import ShellSection
from app.solve.shell_loads import assemble_shell_loads
from app.solve.types import LoadCase, SolverError


class ShellSolver:
    """Linear static on a shell mesh, through CalculiX.

    The constructor mirrors `CalculiXSolver`'s and for the same reason: a solver
    that read the binary's location from global settings could not be tested at
    two settings in one process.
    """

    name = "calculix-shell"

    def __init__(
        self,
        *,
        executable: str | os.PathLike[str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        threads: int | None = None,
    ) -> None:
        self.executable = executable
        self.timeout_s = timeout_s
        self.threads = threads

    def solve(self, mesh: ShellMesh, case: LoadCase, section: ShellSection) -> SolveOutput:
        started = time.perf_counter()
        deck, warnings = write_shell_deck(mesh, case, section)

        run = run_ccx(
            deck,
            executable=self.executable,
            timeout_s=self.timeout_s,
            threads=self.threads,
        )

        failure = diagnose(run)
        if failure is not None:
            raise SolverError(_with_evidence(failure))

        frd = parse_frd(run.frd)
        node_displacements = displacements(frd, mesh.node_count)
        nodal_tensor = nodal_stress_tensor(frd, mesh.node_count)
        mises = shell_von_mises(mesh, nodal_tensor)

        seconds = time.perf_counter() - started
        warnings = [*warnings, *_warnings_from(run, frd)]

        return SolveOutput(
            result=summarise_shell_static(
                mesh, case, section.thickness_mm, node_displacements, mises, warnings, seconds
            ),
            displacements=node_displacements,
            von_mises=mises,
            nodal_stress=nodal_tensor,
        )


def write_shell_deck(
    mesh: ShellMesh, case: LoadCase, section: ShellSection
) -> tuple[str, list[str]]:
    """The deck for a shell `LoadCase`, and any warnings raised distributing it.

    Public and separate from `solve` **because it is the half that can be tested
    without `ccx`** — there is none on the Linux machine where most of this was
    written, so a function that both built and ran the deck would have made the
    build untestable there and left the writing to be checked on the one machine
    that could also run it. Every assertion about what this repo asks CalculiX
    to do is made against this string.

    The warnings are returned rather than swallowed because
    `assemble_shell_loads` has one that matters: a load region matching no whole
    face falls back to an equal split between its nodes, which delivers the
    right resultant and the wrong distribution. That is the common case for an
    edge load — see `app/solve/shell_loads.py` — so it must reach the caller.
    """
    forces, warnings = assemble_shell_loads(
        mesh, list(case.loads), section.thickness_mm, case.material.density_kg_m3
    )
    deck = write_frame_deck(
        mesh,
        case.material,
        case.fixtures,
        section,
        forces=forces,
        delta_t_k=case.delta_t_k,
        name=case.name or "Kryova",
    )
    return deck, warnings


def shell_von_mises(
    mesh: ShellMesh, nodal_tensor: NDArray[np.float64]
) -> NDArray[np.float64]:
    """(n_faces,) von Mises in MPa from an (n_nodes, 6) nodal stress field.

    The tensor is averaged onto the face first and the invariant taken once,
    never the other way round: von Mises is nonlinear, so averaging the
    invariant is biased high, and biased high exactly where a factor of safety
    is read. `CalculiXSolver.element_von_mises` states the same rule for solids
    and this is deliberately its twin, down to the order of the two calls.
    """
    return von_mises_from_tensor(shell_element_average(mesh, nodal_tensor))


__all__ = ["ShellSolver", "shell_von_mises", "write_shell_deck"]
