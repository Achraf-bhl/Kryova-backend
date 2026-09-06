"""Traceability, both ways — master plan **11.3**.

*"Every design decision to a requirement or a standard — what a signing engineer
demands first."* Two questions, and a signing engineer asks both:

* **Why is this rib here?** From a feature, back to the requirements that justify
  it, and from those up the decomposition graph to the customer or the standard
  that started it (`RequirementSet.ancestors_of`).
* **What satisfies REQ-014?** From a requirement, down to the features, the plan
  calls and the assertions that exist because of it.

**The links are read out of what is already there, not declared beside it.**
`app.design.spec.FeatureSpec` has carried a `note` since the design IR was
written, for precisely this purpose — *"why is this rib here" must have an answer
in six months, and the place that answer survives is next to the rib* — and
`PlannedCall` carries it through compilation, and `Assertion` carries one too. So
a feature note reading `"REQ-014: stiffens the flange under the panic-brake
case"` **is** the trace link. Nothing new is stored, nothing has to be kept in
step, and a link cannot rot into disagreeing with the design because it is part
of the design.

The alternative — a `satisfied_by` list on the requirement — was rejected for the
usual reason a second copy of the truth is rejected: nothing checks it, so it is
correct exactly until somebody renames a feature.

**Precision is the hard half, and it is solved by only recognising ids that
exist.** Scanning free prose for `[A-Z]+-[0-9]+` finds `ISO 898-1`, `2006/42/EC`,
`V5R21` and half the hyphenated English language. So a *link* is only recorded
for an id that is actually in the requirement set — an exact, case-sensitive,
token-bounded match — which makes the recognition exact rather than heuristic.
The loose pattern is used for one thing only: finding citations that look like a
requirement id and are **not** in the set, which is how a note left behind by a
renamed or deleted requirement gets reported instead of silently meaning nothing.
Those come back as `dangling`, and they are the reason this module bothers with a
pattern at all.

**A requirement nothing cites is reported.** `untraced` is the output that earns
this module: a requirement that no feature, call or assertion says it is the
reason for has not been designed against, however green its verification looks —
it may simply be true by accident of the shape somebody drew.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.requirements.model import RequirementSet

#: What a citation looks like when we do not already know the id. Deliberately
#: narrow: an upper-case prefix, a separator, then a **digit**. `ISO 898-1` does
#: not match (the token starts with a digit), `mm-N-MPa` does not (no digit after
#: a separator), `well-formed` does not, `V5R21` does not (no separator). It is
#: used only to find citations of ids that are *not* in the set — real links are
#: matched exactly against the ids that are.
CITATION_PATTERN: Final = re.compile(r"\b[A-Z][A-Z0-9]{1,15}[-_.][0-9][A-Za-z0-9._-]*\b")

#: Where a note came from. Words rather than an enum because they are printed in
#: a traceability matrix and never branched on.
FEATURE: Final = "feature"
CALL: Final = "call"
ASSERTION: Final = "assertion"
DESIGN: Final = "design"


@dataclass(frozen=True)
class Note:
    """One piece of rationale somewhere in the design, and where it was written.

    The unit this module works in. Adapters below turn a `DesignSpec`, a `Plan`
    or a list of assertions into these, so `trace` itself imports nothing from
    the design package and can be handed rationale from somewhere neither of
    them knows about — a bought-in part's justification, a meeting note attached
    to a project.
    """

    kind: str
    target: str
    text: str


@dataclass(frozen=True)
class TraceLink:
    """One requirement, and one place in the design that says it is the reason."""

    requirement_id: str
    kind: str
    target: str
    text: str

    def __str__(self) -> str:
        return f"{self.requirement_id} <- {self.kind} {self.target}: {self.text}"


@dataclass(frozen=True)
class DanglingCitation:
    """Something that reads like a requirement id and is not one in this set.

    Almost always a requirement that was renamed or retired, leaving a feature
    note that still explains itself by reference to it. Reported rather than
    ignored, because the note is now rationale pointing at nothing and the next
    person to read it will believe it.
    """

    citation: str
    kind: str
    target: str
    text: str

    def __str__(self) -> str:
        return (
            f"{self.kind} {self.target} cites {self.citation}, which is not in this "
            "requirement set"
        )


@dataclass(frozen=True)
class TraceReport:
    """The traceability matrix, readable from either end."""

    links: tuple[TraceLink, ...] = ()
    untraced: tuple[str, ...] = ()
    dangling: tuple[DanglingCitation, ...] = ()

    def evidence_for(self, requirement_id: str) -> tuple[TraceLink, ...]:
        """Everything in the design that says it exists because of this requirement."""
        return tuple(one for one in self.links if one.requirement_id == requirement_id)

    def requirements_for(self, target: str) -> tuple[str, ...]:
        """Which requirements justify this feature, call or assertion.

        The "why is this rib here" direction. Ids in the order they were cited,
        de-duplicated — a feature whose note and whose compiled call both cite
        REQ-014 is justified by it once, not twice.
        """
        found: list[str] = []
        for one in self.links:
            if one.target == target and one.requirement_id not in found:
                found.append(one.requirement_id)
        return tuple(found)

    def targets_for(self, requirement_id: str) -> tuple[str, ...]:
        found: list[str] = []
        for one in self.links:
            if one.requirement_id == requirement_id and one.target not in found:
                found.append(one.target)
        return tuple(found)

    @property
    def traced(self) -> tuple[str, ...]:
        """Requirement ids something in the design cites."""
        found: list[str] = []
        for one in self.links:
            if one.requirement_id not in found:
                found.append(one.requirement_id)
        return tuple(found)

    def matrix(self) -> Mapping[str, tuple[str, ...]]:
        """Requirement id -> the design elements that cite it, untraced ones included.

        The shape a traceability matrix is printed from. Untraced requirements
        carry an empty tuple rather than being absent, because a matrix that only
        lists the rows it could fill is a matrix that looks complete.
        """
        out: dict[str, tuple[str, ...]] = {}
        for requirement_id in self.traced:
            out[requirement_id] = self.targets_for(requirement_id)
        for requirement_id in self.untraced:
            out.setdefault(requirement_id, ())
        return out

    def __bool__(self) -> bool:
        """True when every active requirement is cited and no citation dangles."""
        return not self.untraced and not self.dangling

    def summary(self) -> str:
        total = len(self.traced) + len(self.untraced)
        if not total and not self.dangling:
            return "Nothing to trace."
        lines = [
            f"{len(self.traced)}/{total} requirements are cited by something in the "
            f"design ({len(self.links)} links)."
        ]
        if self.untraced:
            lines.append(
                "Nothing says these are the reason for anything: "
                + ", ".join(self.untraced)
                + ". A requirement no decision cites has not been designed against, "
                "however it verified."
            )
        lines.extend(str(one) for one in self.dangling)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "links": [
                {
                    "id": one.requirement_id,
                    "kind": one.kind,
                    "target": one.target,
                    "text": one.text,
                }
                for one in self.links
            ],
            "untraced": list(self.untraced),
            "dangling": [
                {"citation": one.citation, "kind": one.kind, "target": one.target}
                for one in self.dangling
            ],
        }


def trace(requirements: RequirementSet, notes: Iterable[Note]) -> TraceReport:
    """Build the traceability matrix from a requirement set and the design's rationale.

    Obsolete requirements are matched too, and deliberately: a note citing a
    retired requirement is a real link to a real decision, and reporting it as
    dangling would send someone looking for a typo that is not there. They are
    excluded from `untraced`, though — nobody owes a retired requirement a
    justification.
    """
    collected = tuple(notes)
    known = {one.id: one for one in requirements}
    links: list[TraceLink] = []
    dangling: list[DanglingCitation] = []

    for note in collected:
        text = note.text or ""
        if not text.strip():
            continue
        for requirement_id in known:
            if _cites(text, requirement_id):
                links.append(
                    TraceLink(
                        requirement_id=requirement_id,
                        kind=note.kind,
                        target=note.target,
                        text=text.strip(),
                    )
                )
        for candidate in CITATION_PATTERN.findall(text):
            if candidate in known:
                continue
            dangling.append(
                DanglingCitation(
                    citation=candidate, kind=note.kind, target=note.target, text=text.strip()
                )
            )

    cited = {one.requirement_id for one in links}
    untraced = tuple(one.id for one in requirements.active if one.id not in cited)
    return TraceReport(
        links=tuple(links), untraced=untraced, dangling=tuple(dangling)
    )


def _cites(text: str, requirement_id: str) -> bool:
    """Exact, case-sensitive, token-bounded. `REQ-1` does not match `REQ-14`.

    The boundary is the characters an id may itself contain, so a match ends
    where the id ends and not at the hyphen in the middle of one. The full stop
    is the awkward case and gets its own rule: an id may contain one
    (`SYS-1.2.3`), and a requirement is also cited at the end of a sentence
    (`"Envelope from REQ-001."`). So a dot only blocks the match when a
    letter or digit follows it — which keeps `SYS-1.2` from matching inside
    `SYS-1.2.3` while letting ordinary punctuation end a citation.
    """
    escaped = re.escape(requirement_id)
    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])(?<![A-Za-z0-9]\.)"
        + escaped
        + r"(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9])"
    )
    return bool(pattern.search(text))


# -- adapters: turning the design's own rationale into notes ------------------


def notes_from_spec(spec: Any) -> tuple[Note, ...]:
    """Every note in a `DesignSpec`: the design's description and each feature's.

    Typed loosely on purpose. This package must not depend on the design package
    to *verify* requirements — a requirements document arrives before any
    geometry does — and a hard import here would make `app.requirements` refuse
    to load without it.
    """
    found: list[Note] = []
    description = getattr(spec, "description", "") or ""
    name = getattr(spec, "name", "design")
    if description.strip():
        found.append(Note(kind=DESIGN, target=str(name), text=description))
    for feature in getattr(spec, "features", ()):
        note = getattr(feature, "note", "") or ""
        if note.strip():
            found.append(Note(kind=FEATURE, target=str(feature.name), text=note))
    return tuple(found)


def notes_from_plan(plan: Any) -> tuple[Note, ...]:
    """Every note on a compiled `Plan`'s calls.

    Worth having beside `notes_from_spec` rather than instead of it: the compiler
    writes notes of its own for the calls it emits (creating the document,
    setting the material), and a plan is what actually ran — so a rationale that
    reached the workstation is evidence of a slightly different kind from one
    that was only ever written down.
    """
    found: list[Note] = []
    for call in getattr(plan, "calls", ()):
        note = getattr(call, "note", "") or ""
        if note.strip():
            found.append(
                Note(kind=CALL, target=f"{call.index}:{call.tool}", text=note)
            )
    return tuple(found)


def notes_from_assertions(assertions: Iterable[Any]) -> tuple[Note, ...]:
    """Every note on a list of assertions or machine checks.

    An assertion written *because of* a requirement is the strongest trace link
    there is — it is the requirement being checked — and `Requirement.assertion`
    puts the id in the assertion's name, so a requirement-derived assertion
    traces itself.
    """
    found: list[Note] = []
    for one in assertions:
        text = " ".join(
            part for part in (getattr(one, "name", ""), getattr(one, "note", "")) if part
        )
        if text.strip():
            found.append(Note(kind=ASSERTION, target=str(getattr(one, "name", "")), text=text))
    return tuple(found)


__all__ = [
    "ASSERTION",
    "CALL",
    "CITATION_PATTERN",
    "DESIGN",
    "FEATURE",
    "DanglingCitation",
    "Note",
    "TraceLink",
    "TraceReport",
    "notes_from_assertions",
    "notes_from_plan",
    "notes_from_spec",
    "trace",
]
