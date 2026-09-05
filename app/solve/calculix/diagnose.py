"""What a failed `ccx` run means — master plan 6.6, the failure taxonomy.

A solver that stops is not the end of the answer; it is most of one, and the
codebase's error register says so everywhere else ("Increase element_size_mm to
coarsen it", "Check that the fixtures remove all six rigid-body motions"). The
same has to be true across the process boundary, or a federated solver is a
downgrade: today a singular system comes back from `linear_static` as a sentence
naming the missing restraint, and it must not come back from CalculiX as
`returncode 201`.

**What is asserted here, and what is not.** The patterns below are matched
against `ccx`'s own output. Where a string is quoted from CalculiX's source it
says so in the table, and where it is a family of wordings rather than one exact
message it says that too. **None of them has yet been seen from a real `ccx` on
this machine** — there is no binary installed here — so `Diagnosis.classified`
exists and is `False` whenever nothing matched, and the unmatched `*ERROR` lines
come back verbatim rather than being summarised away. That is the same contract
`frd.describe()` and `catia_describe_dialog` hold: the first real run should
produce the answer rather than a shrug, and a taxonomy that quietly labelled
everything would destroy the evidence it needs.

**Matching is on a lower-cased substring, and the order is significant.** The
specific patterns run before the general ones, because "too many cutbacks" is
also a non-convergence and "nonpositive jacobian" also fails the factorisation:
whichever matches first decides what the user is told to do, and the useful
instruction is always the specific one. The general entries are last on purpose,
as a floor rather than a catch-all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the checker
    from app.solve.calculix.run import CcxRun

#: The kinds 6.6 names, plus the three a real run produces that it does not:
#: a deck the solver would not read, a machine it would not fit on, and the
#: honest "it stopped and we do not know why".
CONVERGENCE: Final = "non-convergence"
SINGULAR: Final = "singular"
DISTORTED: Final = "distorted-elements"
CONTACT: Final = "contact"
DECK: Final = "deck-rejected"
RESOURCES: Final = "out-of-resources"
NO_RESULTS: Final = "no-results"
UNCLASSIFIED: Final = "unclassified"

#: Lines CalculiX prefixes its complaints with. Both are collected as evidence;
#: only `*ERROR` decides that a run failed, because a `*WARNING` is routine —
#: CalculiX warns about zero-volume checks and unused sets on runs that finish
#: perfectly well, and treating those as failures would refuse good answers.
_COMPLAINT_RE: Final = re.compile(r"^\s*\*(ERROR|WARNING)\b.*$", re.MULTILINE)
_ERROR_RE: Final = re.compile(r"^\s*\*ERROR\b.*$", re.MULTILINE)


@dataclass(frozen=True)
class Diagnosis:
    """One reading of why a run did not produce results.

    `summary` says what happened and `what_to_do` says what to change — kept
    apart rather than as one string because the agent acts on the second and
    shows the user the first, and joining them means the correction loop has to
    parse prose to find the instruction.
    """

    kind: str
    summary: str
    what_to_do: str
    #: The solver's own lines that led here, verbatim and unabridged. A summary
    #: that discards its evidence cannot be checked, and this is the text a
    #: human needs when the classification turns out to be wrong.
    evidence: list[str] = field(default_factory=list)
    #: False when nothing in the table matched. Never suppressed, never inferred
    #: from the presence of evidence: an unclassified failure carrying five
    #: error lines is still unclassified.
    classified: bool = True

    def message(self) -> str:
        """One string for a `SolverError`, in the codebase's own register."""
        return f"{self.summary} {self.what_to_do}".strip()


#: `(substring, kind, summary, what_to_do, provenance)` — specific first.
#:
#: `provenance` is not shown to anyone; it records where the string came from so
#: a later session can tell a quotation from a guess without re-deriving it.
#: "source" means the literal text is in CalculiX's Fortran; "family" means the
#: wording varies between versions and this matches the stable part of it.
_TABLE: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    (
        "nonpositive jacobian",
        DISTORTED,
        "CalculiX found an element whose shape is inverted or flat, which makes "
        "its volume zero or negative and its stiffness meaningless.",
        "This is a meshing failure, not a load or restraint one: re-mesh, and if "
        "it recurs at the same place, simplify the geometry there — a sliver "
        "face or an edge shorter than the element size is the usual cause.",
        "source: e_c3d.f",
    ),
    (
        "too many cutbacks",
        CONVERGENCE,
        "The solver reduced its increment as far as it is allowed to and still "
        "could not find equilibrium.",
        "The model is behaving nonlinearly at this load. Reduce the load and "
        "check it converges at all, then raise it — a case that will not "
        "converge at any load usually has a part free to move.",
        "family: nonlingeo cutback loop",
    ),
    (
        "no convergence",
        CONVERGENCE,
        "The solver ran its increments without reaching equilibrium.",
        "Reduce the load until it converges to find the level the model stops "
        "working at; that level is itself the answer to what the part can carry.",
        "family: convergence reporting",
    ),
    (
        "singular",
        SINGULAR,
        "The stiffness matrix is singular: some part of the model can move "
        "without resisting, so there is no unique answer to solve for.",
        "Check that the fixtures remove all six rigid-body motions — three "
        "translations and three rotations. A part held at one face only is free "
        "to spin about it, and a part held nowhere is free entirely.",
        "family: factorisation failure",
    ),
    (
        "zero energy",
        SINGULAR,
        "The solver found a mode that deforms the model at no energy cost, "
        "which is a rigid-body motion that the restraints did not remove.",
        "Add the missing restraint. Which one is missing shows in the "
        "displacement field: the direction everything moves together in.",
        "family: zero-energy mode reporting",
    ),
    (
        "contact",
        CONTACT,
        "The solve failed inside contact: the surfaces kept changing which parts "
        "of them were touching, so no state satisfied the equations twice.",
        "Contact chatter is usually cured by starting the parts in touch rather "
        "than apart, and by making the contact stiffness softer so a small "
        "overlap does not produce a large force.",
        "family: contact iteration",
    ),
    (
        "out of memory",
        RESOURCES,
        "CalculiX could not allocate the memory the factorisation needs.",
        "Coarsen the mesh — the direct solver's memory grows much faster than "
        "the node count — or run it on a machine with more.",
        "family: u_calloc / u_malloc",
    ),
    (
        "u_calloc",
        RESOURCES,
        "CalculiX could not allocate memory.",
        "Coarsen the mesh, or run it where there is more memory. A direct solve "
        "needs several times what the mesh itself occupies.",
        "source: u_calloc.c",
    ),
    (
        "*error reading",
        DECK,
        "CalculiX refused the input deck before solving anything.",
        "This is a bug in the deck Kryova wrote, not in the model as posed — the "
        "solver's message names the keyword and the line. Report it with the "
        "line quoted; nothing about the geometry or the load case will fix it.",
        "source: calinput.f",
    ),
    (
        "*error in calinput",
        DECK,
        "CalculiX refused the input deck while reading it.",
        "This is a bug in the deck Kryova wrote. The solver's message names the "
        "keyword it stopped at; nothing about the model will fix it.",
        "source: calinput.f",
    ),
    (
        "cannot open",
        DECK,
        "CalculiX could not open a file it expected to find.",
        "The run directory did not contain what the job name promised. This is "
        "an integration fault rather than a model one.",
        "family: file opening",
    ),
)


def complaints(output: str) -> list[str]:
    """Every `*ERROR` and `*WARNING` line, verbatim and in order."""
    return [match.group(0).strip() for match in _COMPLAINT_RE.finditer(output)]


def errors(output: str) -> list[str]:
    """Only the `*ERROR` lines. A warning is not a failure."""
    return [match.group(0).strip() for match in _ERROR_RE.finditer(output)]


def diagnose(run: CcxRun) -> Diagnosis | None:
    """Why this run has no usable results, or `None` when it succeeded.

    Success is decided by the output rather than by the return code, because
    `ccx` reports most failures by printing `*ERROR` and stopping — on some
    builds still exiting 0, and still leaving a truncated `.frd` behind. So a
    run that wrote results *and* said no `*ERROR` is the only one treated as
    good; everything else gets classified.
    """
    said = errors(run.output)
    if run.wrote_results and not said:
        return None

    haystack = run.output.lower()
    for needle, kind, summary, what_to_do, _provenance in _TABLE:
        if needle in haystack:
            return Diagnosis(
                kind=kind,
                summary=summary,
                what_to_do=what_to_do,
                evidence=complaints(run.output),
                classified=True,
            )

    if said:
        return Diagnosis(
            kind=UNCLASSIFIED,
            summary="CalculiX stopped with an error Kryova does not recognise.",
            what_to_do=(
                "The solver's own words are the best available account and are "
                "quoted with this. Please report them: an unrecognised failure "
                "is a gap in the diagnosis table, and the text is what closes it."
            ),
            evidence=said,
            classified=False,
        )

    return Diagnosis(
        kind=NO_RESULTS,
        summary=(
            f"CalculiX finished (exit code {run.returncode}) without writing any "
            "results."
        ),
        what_to_do=(
            "A run that produces no results and no error usually means the deck "
            "asked for none — the step must request the fields it wants. If the "
            "deck does request them, report this: it is an integration fault."
        ),
        evidence=complaints(run.output),
        classified=False,
    )


__all__ = [
    "CONTACT",
    "CONVERGENCE",
    "DECK",
    "DISTORTED",
    "NO_RESULTS",
    "RESOURCES",
    "SINGULAR",
    "UNCLASSIFIED",
    "Diagnosis",
    "complaints",
    "diagnose",
    "errors",
]
