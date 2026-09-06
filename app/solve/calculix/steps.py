"""The analysis-step half of a CalculiX deck — master plan 6.4.

`deck.py` writes the *model*: nodes, elements, material, section, the fixture
node sets. This writes the *step* that says what to do with it, and the two are
separated because the model is the same whichever question is asked. A
`*FREQUENCY` step and a `*BUCKLE` step over one part differ in about eight lines;
duplicating a node writer to get them would be the worst possible trade.

Everything here is a pure function returning `list[str]`. No filesystem, no
subprocess, no mesh: a step writer takes the case and the data lines its caller
has already produced, and hands back deck lines. That makes the whole module
testable in microseconds, which is the same property `app/design/` has and for
the same reason.

**There is no `ccx` on the machine this was written on, so not one line below has
been round-tripped through the real solver.** `frd.py` and `diagnose.py` say the
same about themselves and it is worth repeating rather than assuming inherited:
what follows is written from the documentation and from CalculiX's own input
readers, and the first real run is the measurement that turns it from documented
into verified.

Where the documentation comes from
----------------------------------
**Not from this repository.** `app/retrieval/` indexes CATIA manuals and holds
nothing from CalculiX — `python -m app.retrieval.build --query "CalculiX
FREQUENCY step eigenvalue modal analysis"` returns the CATIA GPS/EST buckling
chapter, which is a different program's vocabulary and would be a wrong keyword
dressed as a citation. So every keyword below is quoted from:

- **[M]** CalculiX CrunchiX USER'S MANUAL (Guido Dhondt), keyword sections
  `*FREQUENCY`, `*BUCKLE`, `*STEP` and `*NODE FILE`.
- **[S]** ccx's own source, which is where the manual stops: `frequencys.f` and
  `buckles.f` (the input readers), `writeev.f` and `writebv.f` (the `.dat`
  tables), `arpack.c` and `arpackbu.c` (what reaches the `.frd`). Read for the
  format facts only — Decision 4 forbids linking GPL code, not reading it.

What the documentation actually said
------------------------------------
**`*FREQUENCY` takes the mode count on its own line.** [M]: "First line:
*FREQUENCY. Second line: Number of eigenfrequencies desired. Lower value of
requested eigenfrequency range (in cycles/time; default:0). Upper value of
requested eigenfrequency range (in cycles/time; default: [infinity])."

**The frequency range is deliberately left off, and writing it would lose
modes.** The manual's stated default for the lower bound is 0, which reads as
harmless. It is not the same thing as omitting the field: `frequencys.f`
initialises `fmin=-1.d0` and only overwrites it when the field is *non-blank*,
and `writeev.f` filters with `if(xmin.gt.-0.5d0) then if(xmin*xmin.gt.x(j))
cycle`. So an omitted bound means "no filter", while an explicitly written `0.0`
means "drop every mode whose eigenvalue is negative" — silently, from the `.dat`
table and (`arpack.c`, same test before the `frd` call) from the results file
too. A negative eigenvalue is exactly the finding a modal run must not swallow.
Hence: the count, and nothing else. [S]

**`PERTURBATION` is not written, and that is what makes this an unloaded modal
analysis.** [M], `*STEP`: "The parameter PERTURBATION is allowed for *FREQUENCY,
*BUCKLE, ... If it is specified in a *FREQUENCY, *BUCKLE or *GREEN procedure, the
last *STATIC step is taken as reference state and used to calculate the stiffness
matrix." And [M], `*FREQUENCY`: "If the PERTURBATION parameter is used in the
*STEP card, the load active in the last *STATIC step, if any, will be taken as
preload. Otherwise, no preload will be active." `ModalCase` carries no loads on
purpose, so no preload is the right answer and a bare `*STEP` is how you ask for
it. Stress-stiffened modal analysis is a different case type and would need one.

**A `*BUCKLE` step carries its own load, and the load must be written after the
keyword.** [M]: "All loads previous to a perturbation step are removed at the
start of the step; only the load specified within the buckling step is scaled
till buckling occurs." The manual's wording is about perturbation steps;
`buckles.f` is unconditional — reading the `*BUCKLE` card sets `nforc=0`,
`nload=0`, `nbody=0`, `iprestr=0` under the comment "removing the natural
boundary conditions". So a `*CLOAD` written *before* `*BUCKLE` is discarded with
no error and the run reports the buckling factor of an unloaded structure. The
order below is not cosmetic. [S]

**`*BUCKLE`'s data line is count, accuracy, Lanczos vectors, iterations** [M]:
"Second line: Number of buckling factors desired (usually 1). Accuracy desired
(default: 0.01). # Lanczos vectors calculated in each iteration (default: 4 *
#eigenvalues). Maximum # of iterations (default: 1000). It is rarely needed to
change the defaults." Only the count is written here — `buckles.f` supplies each
of the other three when its field is absent or non-positive, so writing them
would be restating ccx's defaults in our source where they can drift out of step
with it.

**`SOLVER=` is not written either.** [M] gives the default as "the first solver
which has been installed of the following list: SGI, PaStiX, PARDISO, SPOOLES and
TAUCS". Naming one pins the deck to a build that may not have it; `buckles.f`
answers an unknown name with `*WARNING reading *BUCKLE: unknown solver; the
default solver is used`, so naming one wrongly is a warning and a surprise rather
than an error. Let the installed build choose.

**`U` is what puts a mode shape in the `.frd`.** [M], `*NODE FILE`: "U
[DISP(real), DISPI(imaginary)]: Displacements", stored "in file jobname.frd for
subsequent viewing by CalculiX GraphiX". That block name, `DISP`, is the one
`frd.DISPLACEMENT` already looks for.

**A modal step asks for no stresses, and a buckling step does.** A mode shape is
normalised, not scaled to anything physical — `arpack.c` divides each eigenvector
by `sqrt(z^T M z)` — so the stress "of a mode" has no magnitude anybody may quote,
and `*EL FILE, S` in a `*FREQUENCY` step would multiply the results file by the
mode count to store numbers that must not be read. A `*BUCKLE` step is different:
`arpackbu.c` writes the *static* pre-buckling solution as the first results block
of the run, and that one does carry a real stress state, which is what lets one
buckling run also report the stress the load factor multiplies.

Stated assumptions, where the documentation does not settle it
--------------------------------------------------------------
- **That `*NODE FILE` and `*EL FILE` are honoured inside `*FREQUENCY` and
  `*BUCKLE` steps at all** is read from `arpack.c`/`arpackbu.c` calling `frd()`
  per mode with the same `filab` array a static step uses, not from a sentence in
  the manual saying so. It is the mechanism cgx animates modes with, so it is
  very likely right; it is still inference.
- **That `*BOUNDARY` belongs inside the step** rather than before the first one.
  Both are legal; inside is what `deck.py` already does for `*STATIC`, and one
  convention beats two. [M] notes for `*FREQUENCY` that "At the start of a
  frequency calculation all single point constraint boundary conditions, which
  may be zero due to previous steps, are set to zero", so the homogeneous
  restraints written here are what the procedure wants either way.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.solve.types import BucklingCase, ModalCase, SolverError

#: The output requests each step makes. Named rather than inlined so a reader can
#: see at a glance that the modal step asks for less than the buckling one, and
#: so the reason for the difference has somewhere to live.
MODAL_NODE_OUTPUT: Final[tuple[str, ...]] = ("U",)
BUCKLING_NODE_OUTPUT: Final[tuple[str, ...]] = ("U",)
BUCKLING_ELEMENT_OUTPUT: Final[tuple[str, ...]] = ("S",)


def _integer(value: int) -> str:
    """A count as CalculiX reads it: `read(textpart(1)(1:10),'(i10)')`.

    Plain `str` of an int is already right for that; the function exists so the
    field-width fact has a place to be recorded rather than being assumed.
    """
    return str(int(value))


def _block(keyword: str, lines: Sequence[str]) -> list[str]:
    """A keyword card and its data lines, or nothing when there are none.

    An empty block is not harmless. `*BOUNDARY` with no data line underneath is
    how a free-free modal case would come out if this returned the card anyway,
    and ccx answers a card whose definition does not continue with an input error
    naming a line number — the deck would be refused for having no restraints,
    which is the one thing a free-free run is *asking* for.
    """
    if not lines:
        return []
    return [keyword, *lines]


def frequency_step(
    case: ModalCase,
    *,
    boundary: Sequence[str] = (),
) -> list[str]:
    """The `*FREQUENCY` step for one `ModalCase`, as deck lines.

    `boundary` is the *data* lines of the restraint block — `"FIX1, 1, 1, 0.0"`
    and friends — without the `*BOUNDARY` card, which this writes. Empty is
    legal and means free-free: `ModalCase.fixtures` is optional precisely so that
    analysis can be asked for, and no card is written for it.

    The model half — nodes, elements, material, section, the node sets these
    lines name — is `deck.py`'s and is not touched here.
    """
    modes = int(case.modes)
    if modes < 1:
        raise SolverError(
            f"A frequency step must ask for at least one mode; {modes} was requested. "
            "Set ModalCase.modes to the number of natural frequencies you want."
        )

    lines: list[str] = ["*STEP", "*FREQUENCY", _integer(modes)]
    lines.extend(_block("*BOUNDARY", boundary))
    lines.append("*NODE FILE")
    lines.append(", ".join(MODAL_NODE_OUTPUT))
    lines.append("*END STEP")
    return lines


def buckle_step(
    case: BucklingCase,
    *,
    boundary: Sequence[str] = (),
    loads: Sequence[str] = (),
) -> list[str]:
    """The `*BUCKLE` step for one `BucklingCase`, as deck lines.

    `boundary` and `loads` are data lines without their cards, the same shape
    `frequency_step` takes; `loads` are `*CLOAD` rows, `"12, 3, -250.0"`.

    **`loads` may not be empty**, and that is a refusal rather than a default.
    A buckling factor is a multiplier on the load in the step, so a step with no
    load has nothing to multiply: ccx would run, find no compressive stress
    anywhere, and report factors that mean nothing. `BucklingCase` requires at
    least one load, so an empty block here means every force was distributed to
    zero — which the caller can fix and the solver cannot.
    """
    modes = int(case.modes)
    if modes < 1:
        raise SolverError(
            f"A buckling step must ask for at least one factor; {modes} was requested. "
            "Set BucklingCase.modes to the number of buckling modes you want."
        )
    if not loads:
        raise SolverError(
            "A buckling step was written with no load. A buckling factor is the number "
            "the load in the step is multiplied by, so a step with no load has nothing "
            "to report a factor for. Check that the load case's forces reach the mesh: "
            "a selector that lands on no node distributes to zero everywhere."
        )
    if not boundary:
        raise SolverError(
            "A buckling step was written with no restraint. An unrestrained structure "
            "moves rather than buckles, and the eigenproblem behind the factor has no "
            "answer. Give the case at least one fixture that removes all six rigid-body "
            "motions."
        )

    lines: list[str] = ["*STEP", "*BUCKLE", _integer(modes)]
    lines.extend(_block("*BOUNDARY", boundary))
    # After the *BUCKLE card, never before it: reading the card zeroes every load
    # accumulated so far (`buckles.f`), so a *CLOAD written above would vanish
    # without a word. See the module docstring.
    lines.extend(_block("*CLOAD", loads))
    lines.append("*NODE FILE")
    lines.append(", ".join(BUCKLING_NODE_OUTPUT))
    lines.append("*EL FILE")
    lines.append(", ".join(BUCKLING_ELEMENT_OUTPUT))
    lines.append("*END STEP")
    return lines


__all__ = [
    "BUCKLING_ELEMENT_OUTPUT",
    "BUCKLING_NODE_OUTPUT",
    "MODAL_NODE_OUTPUT",
    "buckle_step",
    "frequency_step",
]
