"""`CalculiXSolver` — master plan 6.1, the four parts joined.

`deck.py` writes, `run.py` runs, `frd.py` reads, `diagnose.py` explains. This is
the `Solver` implementation that puts them in a line, and its whole claim is that
**the ABC does not change**: the job layer, the API and the agent cannot tell
which solver ran, which is the seam `CLAUDE.md` says must never be reached
around. `LinearStaticSolver` and this one are interchangeable at the callsite.

Two conversions happen here and nowhere else, and both are the kind that produce
a plausible wrong number rather than an error.

**CalculiX reports stress at nodes; `SolveOutput` carries it per element.** The
`.frd` holds nodal values, extrapolated from the integration points and averaged
across every element meeting at that node. Going back to elements is therefore a
*re-averaging*, and it is done on the **tensor**, not on the von Mises invariant:
von Mises is nonlinear, so averaging six components and then taking the invariant
is not the same number as taking the invariant six times and averaging it — the
second is biased high, and biased high in exactly the place a factor of safety is
read. `linear_static`'s own `von_mises` is then applied to the result, so both
solvers compute the invariant with one function (Decision 2: a second copy of a
formula is a second thing to drift).

The averaging itself is `postprocess.element_average`, beside `nodal_average`,
which is the same bridge in the other direction; the corner-node rule and why it
is corner-only are recorded there.

**A nodal field is smoothed and an element field is not**, and the difference is
physical rather than numerical. In a uniform stress state — a bar in tension —
the two agree to machine precision, which is why 6.5's oracle test is posed
there. At a stress concentration they will not agree, and the honest reading is
that CalculiX's extrapolated-and-averaged peak is *lower* than the in-house
constant-per-element peak. That is not a disagreement about the physics; a test
that pinned them equal at a notch would be pinning a coincidence.
"""

from __future__ import annotations

import os
import time

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.base import SolveOutput, Solver
from app.solve.calculix.deck import write_deck
from app.solve.calculix.diagnose import Diagnosis, complaints, diagnose
from app.solve.calculix.frd import (
    FrdFile,
    displacements,
    nodal_stress_tensor,
    parse_frd,
    von_mises_from_tensor,
)
from app.solve.calculix.run import DEFAULT_TIMEOUT_S, CcxRun, run_ccx
from app.solve.postprocess import element_average, summarise_static
from app.solve.types import LoadCase, SolverError


class CalculiXSolver(Solver):
    """Linear static through CalculiX, across a subprocess boundary.

    The constructor takes where the binary is and how long to wait rather than
    reading them from settings, for the reason every other module here does the
    same: a solver that reads global configuration cannot be tested at two
    settings in one process, and the sweep in 5.3 needs exactly that.
    """

    name = "calculix"

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

    def solve(self, mesh: TetMesh, case: LoadCase) -> SolveOutput:
        started = time.perf_counter()
        deck = write_deck(mesh, case)

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
        mises = element_von_mises(mesh, nodal_tensor)

        seconds = time.perf_counter() - started
        warnings = _warnings_from(run, frd)

        return SolveOutput(
            result=summarise_static(
                mesh, case, node_displacements, mises, warnings, seconds
            ),
            displacements=node_displacements,
            von_mises=mises,
        )


def element_von_mises(
    mesh: TetMesh, nodal_tensor: NDArray[np.float64]
) -> NDArray[np.float64]:
    """(n_elements,) von Mises in MPa from an (n_nodes, 6) nodal stress field.

    The averaging itself is `postprocess.element_average`, which is where the
    corner-node rule lives and where `nodal_average` — the same bridge in the
    other direction — already lived. What this adds is the **order**: average
    the tensor, then take the invariant once. Doing it the other way round is
    biased high, and biased high exactly where a factor of safety is read.
    """
    return von_mises_from_tensor(element_average(mesh, nodal_tensor))


def _warnings_from(run: CcxRun, frd: FrdFile) -> list[str]:
    """What the run said that the caller should see but that did not stop it.

    Unrecognised `.frd` records are surfaced here rather than logged, because a
    parser meeting a real file for the first time finding something it cannot
    classify is a finding — and a finding that only reaches a log file is one
    nobody reads.
    """
    out = [line for line in complaints(run.output) if "*WARNING" in line.upper()]
    if frd.unrecognised:
        out.append(
            f"The results file held {len(frd.unrecognised)} record(s) this parser "
            "does not recognise; they were skipped. The stresses and displacements "
            "reported are unaffected, but please report this — an unrecognised "
            "record is a gap in the reader."
        )
    return out


#: How many of the solver's own lines are quoted before the rest are counted.
#: Enough to carry the several lines CalculiX prints around a real failure, and
#: short of the hundreds a deck error can produce, which would bury the
#: diagnosis under its own evidence.
_QUOTED_LINES = 20


def _with_evidence(failure: Diagnosis) -> str:
    """The diagnosis, then the solver's own words underneath it.

    Both, always. The diagnosis is what the agent acts on and the raw text is
    what a human checks it against; dropping either leaves one of the two
    readers with nothing.

    Takes the `Diagnosis` rather than re-deriving one from the run: classifying
    twice is not only wasted work, it is two answers that can disagree, and the
    message would then quote evidence for a different reading than the one being
    raised.
    """
    if not failure.evidence:
        return failure.message()
    quoted = chr(10).join(f"  {line}" for line in failure.evidence[:_QUOTED_LINES])
    hidden = len(failure.evidence) - _QUOTED_LINES
    more = "" if hidden <= 0 else f"{chr(10)}  ... and {hidden} more"
    return f"{failure.message()}{chr(10)*2}CalculiX said:{chr(10)}{quoted}{more}"


__all__ = ["CalculiXSolver", "element_von_mises"]
