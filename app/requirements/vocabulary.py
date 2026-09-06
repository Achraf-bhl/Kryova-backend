"""What a requirement is allowed to constrain, and where each quantity comes from.

Master plan **11.2** — *"a requirement nothing checks is a wish."* The other half
of that sentence is the one this module enforces: a requirement that *claims* to
check something nothing can measure is a wish with a number on it, which is
worse, because it survives review.

`app.kernel.contract` already solved this problem one layer down for assertions:
every quantity a design may assert on is written down once, with its unit and its
meaning, and `undocumented_paths()` is asserted empty so a backend cannot invent
a spelling. This module is that contract *read from above*, plus the one thing a
requirement can constrain that a kernel never measures.

**Two origins, and both are needed.**

* **The measurement contract** (`app.kernel.contract`) — what the part *is* and
  what it can be *made into*: `mass_kg`, `bounding_box_mm.size[2]`,
  `minimum_wall_mm`, `minimum_draft_deg`. Authoritative, versioned, and the
  source of the unit.
* **The machine-check namespace** (`app.design.machine_checks`, `machine.*`) —
  numbers that do not exist until something *produces* them: a clearance through
  a motion range, a factor of safety against a named load case, a tolerance
  stack. Requirements land here constantly — "carries 5 kN at a factor of safety
  of 2" is a machine check, not a kernel measurement — so refusing the namespace
  would refuse most of Phase 11's own examples.

**The machine namespace is validated structurally, by its leaf.** A machine path
is `machine.<check name>.<leaf>` (or `machine.<check name>.<load case>.<leaf>`),
and the check name and load case are the *caller's* words — `fos`, `arm_sweep`,
`lift`. There is nothing to check them against and nothing worth checking. What
is checkable is the leaf, because the library that emits them is finite:
`MassBudget` files `kg`, `FactorOfSafety` files `factor_of_safety`, `StackUp`
files `variation_mm`. So `MACHINE_LEAVES` is that table, and
`tests/test_requirements_vocabulary.py` instantiates every `MachineCheck` in the
library and asserts each path it emits resolves here. The table is kept in step
with the source of truth by a test rather than by discipline — the same
arrangement `assertions.counts_things` has with the contract's `unit="count"`.

**The kernel contract is imported lazily**, exactly as `assertions._provenance`
does and for the same measured reason: `import app.kernel.contract` executes
`app/kernel/__init__.py`, which reaches the OCCT binding and costs ~1.2 s and
~166 MB. A requirement set must be readable, writable and *validatable* on a
machine with no geometry kernel — that is the whole point of a requirements
document arriving before any geometry does. After the first call it is a
`sys.modules` lookup, so construction-time refusal costs nothing per requirement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from app.design import machine_checks
from app.requirements.errors import VocabularyError

#: The namespace machine checks file their produced numbers under, taken from the
#: module that owns the word rather than copied — a rename there must not leave
#: this silently accepting a namespace nobody writes to any more. Importing it
#: costs nothing: the design package holds its "offline, no kernel, under a
#: second" property precisely so it can be imported from anywhere.
MACHINE_NAMESPACE: Final = machine_checks.NAMESPACE

#: Where each term came from. Two words rather than an enum: this is printed in
#: an error message and in a coverage report, and never branched on.
CONTRACT_ORIGIN: Final = "measurement contract"
MACHINE_ORIGIN: Final = "machine check"

_INDEX_SUFFIX: Final = re.compile(r"\[\d+\]$")


@dataclass(frozen=True)
class Term:
    """One quantity a requirement may constrain."""

    path: str
    unit: str
    summary: str

    #: Which of the two vocabularies above this came from, in words.
    origin: str


#: The leaf of every path `app.design.machine_checks` can produce, with the unit
#: it is in. The check name and any load case in between are the caller's, so
#: only the leaf is fixed and only the leaf is checked.
#:
#: **mm-N-MPa, and nothing converts.** A requirement writes `<= 850` against
#: `machine.mass.kg` and means kilograms because that is what the path is in.
MACHINE_LEAVES: Final[dict[str, tuple[str, str]]] = {
    "kg": ("kg", "Mass a mass budget measured. Kilograms, like every mass here."),
    "x": ("mm", "Extent along X, from an envelope check's bounding box."),
    "y": ("mm", "Extent along Y, from an envelope check's bounding box."),
    "z": ("mm", "Extent along Z, from an envelope check's bounding box."),
    "minimum_mm": (
        "mm",
        "Smallest distance a check found — a wall thickness, or the closest approach "
        "through a motion range. Usually sampled, and the provenance sidecar says so.",
    ),
    "hz": ("Hz", "First natural frequency, from a modal analysis of the subject."),
    "factor_of_safety": (
        "ratio",
        "Factor of safety against the named load case. Dimensionless, so a bound on it "
        "is a bare number.",
    ),
    "factor": (
        "ratio",
        "Buckling factor against the named load case: the multiple of the applied load "
        "at which the member buckles.",
    ),
    "variation_mm": ("mm", "Total variation of a tolerance stack, by the stated method."),
    "nominal_mm": ("mm", "Nominal dimension a tolerance stack closes on."),
    "total": (
        "currency",
        "Total cost from a cost model. There is no cost model in this build (Phase 13 "
        "owns it), so a requirement on this verifies UNMEASURED with that reason.",
    ),
}


def normalise(path: str) -> str:
    """Strip an index suffix, so `bounding_box_mm.size[2]` finds its documented term.

    The same rule `contract.normalise` applies, restated here rather than
    delegated because it must also work on a machine path on a machine with no
    kernel installed.
    """
    return _INDEX_SUFFIX.sub("", str(path).strip())


def resolve(path: str) -> Term | None:
    """The term a measurement path names, or None when nothing measures it."""
    cleaned = normalise(path)
    if not cleaned:
        return None
    machine = _resolve_machine(cleaned)
    if machine is not None:
        return machine
    return _resolve_contract(cleaned)


def require(path: str, *, requirement_id: str = "") -> Term:
    """Resolve a path or refuse it, with a message naming what to write instead.

    Called from `Requirement.__post_init__`, which is the point of the whole
    module: the refusal lands on whoever wrote the requirement, at the moment
    they wrote it, rather than surfacing weeks later as an `UNMEASURED` line in
    a coverage report that reads exactly like an honest gap.
    """
    found = resolve(path)
    if found is not None:
        return found
    where = f"{requirement_id}: " if requirement_id else ""
    return _refuse(where, path)


def _refuse(where: str, path: str) -> Term:
    cleaned = normalise(path)
    contract = _contract()
    if contract is not None:
        superseded = contract.entry(cleaned)
        if superseded is not None and superseded.superseded_by:
            raise VocabularyError(
                f"{where}{path!r} was replaced by {superseded.superseded_by!r}. Write "
                "the requirement against the new path; the old one is kept in the "
                "measurement contract so this message can be given rather than a silence."
            )
    if cleaned.startswith(f"{MACHINE_NAMESPACE}."):
        leaves = ", ".join(sorted(MACHINE_LEAVES))
        raise VocabularyError(
            f"{where}{path!r} is in the machine-check namespace but ends in a leaf no "
            f"check produces. A machine path is 'machine.<check>.<leaf>' — for example "
            f"'machine.fos.lift.factor_of_safety'. Leaves that exist: {leaves}."
        )
    raise VocabularyError(
        f"{where}{path!r} is not a quantity anything in this system measures, so a "
        "requirement on it could never be verified — it would report UNMEASURED "
        "forever and look like an honest gap. Use a path from the measurement "
        "contract (app.kernel.contract.catalogue()) or a machine check's path "
        "('machine.<check>.<leaf>'). If the thing genuinely cannot be measured yet, "
        "write the requirement with no measure and a `needs` saying what would make "
        "it checkable — that is counted as uncovered, which is the truth."
    )


def catalogue() -> tuple[str, ...]:
    """Every path a requirement may name today, with its unit and meaning.

    For an error message, a prompt, or a person asking what they are allowed to
    write. The machine namespace is listed as its shape plus its leaves, because
    the check names in the middle are invented per design and enumerating them
    is not possible.
    """
    lines: list[str] = []
    contract = _contract()
    if contract is not None:
        lines.extend(contract.catalogue())
    else:  # pragma: no cover - only on a build with no kernel package at all
        lines.append(
            "The measurement contract could not be read on this machine, so only "
            "machine-check paths can be validated here."
        )
    lines.extend(
        f"machine.<check>.{leaf} ({unit}) — {summary}"
        for leaf, (unit, summary) in sorted(MACHINE_LEAVES.items())
    )
    return tuple(lines)


def contract_version() -> str:
    """The measurement contract version a verification result should be stamped with.

    Decision 3: a result is bound to what produced it. A requirement verified
    against contract 1.3 and re-read under 2.0 must be readable as having been
    checked under a vocabulary that has since changed.
    """
    contract = _contract()
    if contract is None:  # pragma: no cover - only with no kernel package at all
        return "unknown"
    return str(contract.CONTRACT_VERSION)


# -- the two vocabularies ----------------------------------------------------


def _resolve_machine(path: str) -> Term | None:
    parts = path.split(".")
    if len(parts) < 3 or parts[0] != MACHINE_NAMESPACE:
        return None
    if any(not part for part in parts):
        return None
    found = MACHINE_LEAVES.get(parts[-1])
    if found is None:
        return None
    unit, summary = found
    return Term(path=path, unit=unit, summary=summary, origin=MACHINE_ORIGIN)


def _resolve_contract(path: str) -> Term | None:
    contract = _contract()
    if contract is None:  # pragma: no cover - only with no kernel package at all
        return None
    entry = contract.entry(path)
    if entry is None or entry.superseded_by:
        return None
    return Term(
        path=path,
        unit=str(entry.unit),
        summary=str(entry.summary),
        origin=CONTRACT_ORIGIN,
    )


@lru_cache(maxsize=1)
def _contract() -> Any:
    """`app.kernel.contract`, imported on use — see this module's docstring.

    Returns None rather than raising if the kernel package cannot be imported at
    all. That is not the OCCT-is-not-installed case, which imports fine by
    design; it is the case where someone has deployed the requirements model
    without the geometry package, and the honest behaviour there is that machine
    paths still validate and contract paths cannot be checked — not that every
    requirement in the document is refused.
    """
    try:
        from app.kernel import contract
    except Exception:  # pragma: no cover - defensive; the kernel imports without OCCT
        return None
    return contract


__all__ = [
    "CONTRACT_ORIGIN",
    "MACHINE_LEAVES",
    "MACHINE_NAMESPACE",
    "MACHINE_ORIGIN",
    "Term",
    "catalogue",
    "contract_version",
    "normalise",
    "require",
    "resolve",
]
