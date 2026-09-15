"""Which design rules a process needs, attached from the part's own features -- master plan 13.1, 13.4.

`engine.Rule` checks one limit against one measured quantity. What it could not do alone is
say **which** rules a part owes: a cast housing owes a draft angle and a machined plate does
not, and a machined plate with a pocket owes a corner radius a cutter can reach while one
with no pocket owes nothing of the kind. This module holds that knowledge, per process,
and attaches rules from the features the part was actually built with.

**The numbers are not here, and that is the design rather than a gap.** Every limit a rule
set needs is a figure from a specific foundry's, moulder's, printer's or shop's design
guide: the minimum wall a supplier can fill in A356 is theirs, and it moves with the
supplier. A default typed into this file would be a remembered number with the authority of
the product behind it, which is exactly what Decision 3 forbids. So a rule set names the
**quantity, the direction, the features that trigger it and why**, and the caller supplies
each limit with its source. A limit the process needs and nobody supplied is not skipped:
it is listed as `unset`, and a report with anything unset is not `ok`. A DFM check that
passes because nobody gave it a number is the red build that got switched off.

**Two processes cannot be checked on the measured solid, and say so rather than pretend.**
Sheet metal's rules (bend radius, flange reach, hole-to-bend distance) are properties of the
fold tree, not of the folded solid, and `app/sheetmetal/` already checks them there; the
`sheet` set carries no solid rules and names where its checks live. A weld's size, access
and symbol need a weldment model, which is master plan E17 task 3 and does not exist; the
`welded` set checks the parent material's thinnest wall and names the rest as unchecked.

**"Running in the E5 engine"** is `Attachment.assertions()`: the attached rules as
`app.design.assertions.Assertion`s, which a `DesignSpec`'s correction loop reads like any
other. `check` is the red build: `engine.check_rules` plus the unset list, with the scans
the rules need named so a caller can run them first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.design.assertions import Assertion
from app.rules.engine import Rule, RuleReport, check_rules
from app.rules.errors import RuleError, SourceError


class Process(StrEnum):
    CAST = "cast"
    MACHINED = "machined"
    PRINTED = "printed"
    SHEET = "sheet"
    MOULDED = "moulded"
    WELDED = "welded"


#: The features that remove material into a closed corner a rotating cutter must reach.
_CUTTER_CORNERS: Final = frozenset(
    {"catia_pocket", "catia_groove", "catia_slot", "catia_rib", "catia_shell", "catia_shell_faces"}
)


@dataclass(frozen=True)
class RuleTemplate:
    """A rule without its number: the quantity, the direction, the trigger and the reason."""

    key: str
    measure: str
    comparison: str
    why: str
    #: Tool names that make this rule apply. None applies it to every part.
    applies_to: frozenset[str] | None = None


@dataclass(frozen=True)
class RuleSet:
    process: Process
    templates: tuple[RuleTemplate, ...]
    #: What this process needs that no rule on the measured solid can check, and where
    #: that check lives or which task owns it. Printed on every report.
    not_checked_here: tuple[str, ...] = ()


def _moulding(process: Process, material: str) -> RuleSet:
    return RuleSet(
        process=process,
        templates=(
            RuleTemplate(
                "minimum_wall",
                "minimum_wall_mm",
                ">=",
                f"a wall thinner than the {material} can fill freezes before the cavity is full",
            ),
            RuleTemplate(
                "minimum_draft",
                "minimum_draft_deg",
                ">=",
                "a face with too little draft drags on the tool and scores or sticks on ejection",
            ),
            RuleTemplate(
                "undercuts",
                "undercut_face_count",
                "<=",
                "a face the tool cannot withdraw from needs a side action or a core, or cannot "
                "be made in a two-part tool at all",
            ),
            RuleTemplate(
                "minimum_inside_radius",
                "minimum_concave_radius_mm",
                ">=",
                "a sharp inside corner concentrates stress and "
                + ("hot-tears as the metal shrinks" if process is Process.CAST else "sinks"),
            ),
        ),
    )


RULE_SETS: Final[Mapping[Process, RuleSet]] = {
    Process.CAST: _moulding(Process.CAST, "metal"),
    Process.MOULDED: _moulding(Process.MOULDED, "polymer"),
    Process.MACHINED: RuleSet(
        process=Process.MACHINED,
        templates=(
            RuleTemplate(
                "minimum_inside_radius",
                "minimum_concave_radius_mm",
                ">=",
                "a rotating cutter leaves its own radius in every closed inside corner, so a "
                "tighter corner cannot be milled",
                applies_to=_CUTTER_CORNERS,
            ),
            RuleTemplate(
                "undercuts",
                "undercut_face_count",
                "<=",
                "a face hidden from the spindle axis needs another setup or a special cutter",
            ),
            RuleTemplate(
                "travel_x", "bounding_box_mm.size[0]", "<=", "the part must fit the machine's X travel"
            ),
            RuleTemplate(
                "travel_y", "bounding_box_mm.size[1]", "<=", "the part must fit the machine's Y travel"
            ),
            RuleTemplate(
                "travel_z", "bounding_box_mm.size[2]", "<=", "the part must fit the machine's Z travel"
            ),
        ),
    ),
    Process.PRINTED: RuleSet(
        process=Process.PRINTED,
        templates=(
            RuleTemplate(
                "minimum_wall",
                "minimum_wall_mm",
                ">=",
                "a wall thinner than the process resolves prints broken or not at all",
            ),
            RuleTemplate(
                "open_edges",
                "open_edge_count",
                "<=",
                "a slicer needs a closed solid; an open edge is a hole in the skin",
            ),
            RuleTemplate(
                "build_x", "bounding_box_mm.size[0]", "<=", "the part must fit the build volume in X"
            ),
            RuleTemplate(
                "build_y", "bounding_box_mm.size[1]", "<=", "the part must fit the build volume in Y"
            ),
            RuleTemplate(
                "build_z", "bounding_box_mm.size[2]", "<=", "the part must fit the build volume in Z"
            ),
        ),
        not_checked_here=(
            "overhang angle and support: no overhang quantity is measured on the part yet",
        ),
    ),
    Process.SHEET: RuleSet(
        process=Process.SHEET,
        templates=(),
        not_checked_here=(
            "bend radius, flange reach and hole-to-bend distance are checked on the fold tree "
            "by app/sheetmetal/ (unfold and fold), where those quantities exist; the folded "
            "solid carries none of them",
        ),
    ),
    Process.WELDED: RuleSet(
        process=Process.WELDED,
        templates=(
            RuleTemplate(
                "minimum_wall",
                "minimum_wall_mm",
                ">=",
                "parent material thinner than the process can weld burns through",
            ),
        ),
        not_checked_here=(
            "weld size, torch access and weld symbols need a weldment model, which is master "
            "plan E17 task 3 and does not exist yet",
        ),
    ),
}


@dataclass(frozen=True)
class Limit:
    """One number a rule set needs, and the design guide it came from."""

    value: float
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise SourceError(
                "A process limit needs a source: the supplier's design guide, the standard or "
                "the person who adopted it."
            )


@dataclass(frozen=True)
class Unset:
    key: str
    measure: str
    why: str

    def __str__(self) -> str:
        return f"{self.key} ({self.measure}): no limit was given, and {self.why}"


@dataclass(frozen=True)
class Attachment:
    process: Process
    rules: tuple[Rule, ...]
    unset: tuple[Unset, ...] = ()
    #: Template keys whose trigger features the part does not have.
    not_applicable: tuple[str, ...] = ()
    not_checked_here: tuple[str, ...] = ()

    def assertions(self) -> tuple[Assertion, ...]:
        """The attached rules as assertions, for the design loop (master plan E5)."""
        return tuple(rule.as_assertion() for rule in self.rules)

    def scans_needed(self) -> tuple[str, ...]:
        """The `catia_analysis_part` kinds the attached rules read, sorted."""
        from app.requirements.vocabulary import scans_for

        return scans_for(rule.measure for rule in self.rules)


def attach(
    process: Process | str,
    features: Iterable[str],
    limits: Mapping[str, Limit],
) -> Attachment:
    """The rules `process` owes a part built with these feature tools, at these limits."""
    try:
        chosen = Process(str(process).strip().lower())
    except ValueError as exc:
        raise RuleError(
            f"{process!r} is not a process with a rule set. Use one of: "
            f"{', '.join(p.value for p in Process)}."
        ) from exc
    rule_set = RULE_SETS[chosen]
    keys = {t.key for t in rule_set.templates}
    stray = sorted(set(limits) - keys)
    if stray:
        allowed = ", ".join(sorted(keys)) or "none (this process has no rules on the solid)"
        raise RuleError(
            f"The {chosen} rule set has no rule called {', '.join(stray)}. Its rules are: "
            f"{allowed}. A limit nothing reads would look adopted and check nothing."
        )
    tools = frozenset(features)
    rules: list[Rule] = []
    unset: list[Unset] = []
    not_applicable: list[str] = []
    for template in rule_set.templates:
        if template.applies_to is not None and not (tools & template.applies_to):
            not_applicable.append(template.key)
            continue
        limit = limits.get(template.key)
        if limit is None:
            unset.append(Unset(template.key, template.measure, template.why))
            continue
        rules.append(
            Rule(
                name=f"{chosen}.{template.key}",
                measure=template.measure,
                comparison=template.comparison,
                limit=limit.value,
                source=limit.source,
                rationale=template.why,
                process=chosen.value,
            )
        )
    return Attachment(
        process=chosen,
        rules=tuple(rules),
        unset=tuple(unset),
        not_applicable=tuple(not_applicable),
        not_checked_here=rule_set.not_checked_here,
    )


@dataclass(frozen=True)
class DfmReport:
    attachment: Attachment
    report: RuleReport
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Every attached rule satisfied and none left unset. Nothing attached is not ok."""
        return self.report.ok and not self.attachment.unset

    def summary(self) -> str:
        lines = [self.report.summary()]
        if self.attachment.unset:
            lines.append(
                f"{len(self.attachment.unset)} rule(s) this process needs have no limit, so "
                "this is not a pass: " + "; ".join(str(u) for u in self.attachment.unset)
            )
        for item in self.attachment.not_checked_here:
            lines.append(f"Not checked here: {item}.")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "process": self.attachment.process.value,
            "ok": self.ok,
            "rules": [rule.to_dict() for rule in self.attachment.rules],
            "report": self.report.to_dict(),
            "unset": [
                {"key": u.key, "measure": u.measure, "why": u.why} for u in self.attachment.unset
            ],
            "not_applicable": list(self.attachment.not_applicable),
            "not_checked_here": list(self.attachment.not_checked_here),
            "scans_needed": list(self.attachment.scans_needed()),
            "summary": self.summary(),
        }


def check(attachment: Attachment, measurements: Mapping[str, Any]) -> DfmReport:
    """Check the attached rules against one part's measurements."""
    return DfmReport(attachment=attachment, report=check_rules(attachment.rules, measurements))


__all__ = [
    "RULE_SETS",
    "Attachment",
    "DfmReport",
    "Limit",
    "Process",
    "RuleSet",
    "RuleTemplate",
    "Unset",
    "attach",
    "check",
]
