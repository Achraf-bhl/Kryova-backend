"""Silent corruption on edits, measured like a benchmark -- master plan E22.2.

The 2026 literature on LLM-authored CAD found most nominally successful natural-language edits
changing geometry the instruction did not target. "It produced a valid solid" is not the metric;
whether *only* what was asked for moved is. This module is the harness that turns that into a
number for Kryova, and it deliberately ships **no number**: a rate exists only once a real model
has run a real case set, and the report binds the rate to both.

**The measurement is exact, because it is made after compilation.** `diff.py` argues it: once
both specs are compiled, a feature moved if and only if its resolved calls differ. So each case
carries the edit an engineer would have made (`reference`), and the features that edit reaches
*are* the ones the instruction targets -- nobody types that list, and a formula consequence (the
fillet derived from the thickness) counts as targeted because the reference reaches it too.
Then, for the edit the model made:

* **FAILED** -- the editor raised, or its spec does not compile. Not a nominal success.
* **NO_CHANGE** -- it compiles and builds the same part as before. Not a nominal success: the
  instruction asked for something to move.
* **EXACT** -- it builds the same part as the reference.
* **CLEAN** -- it moved only features the reference moves, but not to the reference's values
  (a wrong number on the right feature). A nominal success, not corrupting.
* **CORRUPTING** -- it moved at least one feature the reference does not. That feature is named.

`corruption_rate` is CORRUPTING over nominal successes (EXACT + CLEAN + CORRUPTING), and it is
`None` -- unmeasured, never 0 -- when nothing succeeded nominally. `exact_rate` is beside it,
because a harness that reported corruption alone would score an editor that never edits as
perfect.

Features that stand on a changed feature and were not themselves edited (`SpecDiff.downstream`)
are not counted: they move because something upstream did, and that upstream feature is already
counted, as targeted or as corruption. A material change the reference does not make is counted
as corruption of the part as a whole, under the name `<material>`.

Offline, like the rest of `app/design/`: the editor is an injected callable, so a model, a
scripted fixture or a person can be measured with the same code, and no kernel is imported.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.design.compile import compile_spec
from app.design.diff import SpecDiff, builds_the_same, diff_plans
from app.design.errors import SpecError
from app.design.spec import DesignSpec

#: The name a whole-part change is counted under.
MATERIAL = "<material>"

Editor = Callable[[DesignSpec, str], DesignSpec]


class CorruptionError(ValueError):
    """A case that cannot measure anything."""


class EditOutcome(StrEnum):
    FAILED = "failed"
    NO_CHANGE = "no_change"
    EXACT = "exact"
    CLEAN = "clean"
    CORRUPTING = "corrupting"

    @property
    def nominal_success(self) -> bool:
        return self in (EditOutcome.EXACT, EditOutcome.CLEAN, EditOutcome.CORRUPTING)


@dataclass(frozen=True)
class EditCase:
    """One instruction, the design it applies to, and the edit that fulfils it."""

    name: str
    before: DesignSpec
    instruction: str
    reference: DesignSpec

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise CorruptionError(f"{self.name}: a case needs the instruction the editor is given.")
        if builds_the_same(compile_spec(self.before), compile_spec(self.reference)):
            raise CorruptionError(
                f"{self.name}: the reference builds the same part as the design, so every "
                "editor would score NO_CHANGE as correct. Give a reference that changes something."
            )

    def digest(self) -> str:
        payload = [self.name, self.instruction, self.before.digest(), self.reference.digest()]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _moved(diff: SpecDiff) -> frozenset[str]:
    """What the edit changed in the built part.

    `changed_calls` covers added, suppressed, unsuppressed and edited features, by their compiled
    calls. A removed feature is in no call list of the new plan, so it comes from `features`.
    The rest of `features` is deliberately not read: it compares the *written* arguments, so a
    formula rewritten as the literal it resolves to is listed there while building the same
    geometry, and counting it would report corruption the part does not have.
    """
    removed = {change.name for change in diff.features if change.kind == "removed"}
    names = set(diff.changed_calls) | removed
    if diff.material is not None:
        names.add(MATERIAL)
    return frozenset(names)


@dataclass(frozen=True)
class CaseResult:
    case: str
    outcome: EditOutcome
    targeted: tuple[str, ...]
    #: Features the edit moved that the reference does not move.
    corrupted: tuple[str, ...] = ()
    #: Features the reference moves that the edit left alone.
    missed: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "outcome": self.outcome.value,
            "targeted": list(self.targeted),
            "corrupted": list(self.corrupted),
            "missed": list(self.missed),
            "detail": self.detail,
        }


def measure_case(case: EditCase, editor: Editor) -> CaseResult:
    """Run one case through the editor and classify what came back."""
    before_plan = compile_spec(case.before)
    reference_plan = compile_spec(case.reference)
    targeted = _moved(diff_plans(case.before, case.reference, before_plan, reference_plan))
    ordered_targets = tuple(sorted(targeted))
    try:
        edited = editor(case.before, case.instruction)
        edited_plan = compile_spec(edited)
    except SpecError as exc:
        return CaseResult(case.name, EditOutcome.FAILED, ordered_targets, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - an editor's crash is a measured failure
        return CaseResult(
            case.name, EditOutcome.FAILED, ordered_targets, detail=f"{type(exc).__name__}: {exc}"
        )
    if builds_the_same(before_plan, edited_plan):
        return CaseResult(
            case.name, EditOutcome.NO_CHANGE, ordered_targets, missed=ordered_targets
        )
    moved = _moved(diff_plans(case.before, edited, before_plan, edited_plan))
    corrupted = tuple(sorted(moved - targeted))
    missed = tuple(sorted(targeted - moved))
    if corrupted:
        outcome = EditOutcome.CORRUPTING
    elif builds_the_same(reference_plan, edited_plan):
        outcome = EditOutcome.EXACT
    else:
        outcome = EditOutcome.CLEAN
    return CaseResult(case.name, outcome, ordered_targets, corrupted, missed)


@dataclass(frozen=True)
class CorruptionReport:
    """A case set run through one editor. The rate means nothing without both names."""

    editor: str
    case_set_digest: str
    results: tuple[CaseResult, ...]
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def nominal_successes(self) -> int:
        return sum(1 for r in self.results if r.outcome.nominal_success)

    @property
    def corruption_rate(self) -> float | None:
        """Corrupting edits over nominally successful ones; None when none succeeded."""
        if self.nominal_successes == 0:
            return None
        corrupting = sum(1 for r in self.results if r.outcome is EditOutcome.CORRUPTING)
        return corrupting / self.nominal_successes

    @property
    def exact_rate(self) -> float | None:
        """Edits that build the reference's part, over every case; None for an empty run."""
        if not self.results:
            return None
        return sum(1 for r in self.results if r.outcome is EditOutcome.EXACT) / len(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "editor": self.editor,
            "case_set_digest": self.case_set_digest,
            "cases": len(self.results),
            "counts": dict(self.counts),
            "nominal_successes": self.nominal_successes,
            "corruption_rate": self.corruption_rate,
            "exact_rate": self.exact_rate,
            "measured_at": "compiled plans: a feature moved iff its resolved calls differ",
            "results": [r.to_dict() for r in self.results],
        }


def measure(cases: Sequence[EditCase], editor: Editor, *, editor_name: str) -> CorruptionReport:
    """Every case through one editor. The editor's name is required, because a rate is its."""
    if not editor_name.strip():
        raise CorruptionError("Name the editor: a model, its version, and how it was prompted.")
    if not cases:
        raise CorruptionError("There are no cases to measure.")
    names = [case.name for case in cases]
    if len(set(names)) != len(names):
        raise CorruptionError("Two cases share a name, so their results could not be told apart.")
    results = tuple(measure_case(case, editor) for case in cases)
    counts = {outcome.value: 0 for outcome in EditOutcome}
    for result in results:
        counts[result.outcome.value] += 1
    digest = hashlib.sha256("".join(case.digest() for case in cases).encode()).hexdigest()
    return CorruptionReport(editor_name, digest, results, counts)


__all__ = [
    "MATERIAL",
    "CaseResult",
    "CorruptionError",
    "CorruptionReport",
    "EditCase",
    "EditOutcome",
    "Editor",
    "measure",
    "measure_case",
]
