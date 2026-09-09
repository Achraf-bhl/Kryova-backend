"""A requirement as data, and the graph requirements form.

Master plan **Phase 11.1**. This is the phase that turns *"make me a 200×150
plate weighing 2.4 kg"* into *"make me something that carries 5 kN at a factor of
safety of 2 and fits in this envelope"* — the input stops being a shape
description and becomes a specification the shape is judged against.

**A requirement is an assertion with a source, a rationale and a place in a
graph.** That sentence is the whole design, and every part of it is load-bearing:

* **An assertion** — so `app.design.assertions` does the comparing. `Outcome` is
  reused, not re-declared: `PASSED`, `FAILED`, and `UNMEASURED` which is never a
  pass. There is deliberately no second verdict type in this package. A
  requirement compiles to an `Assertion` named after its id, which is exactly
  master plan **5.2**: a failing report says *"REQ-014 not met"*, not
  *"mass_kg <= 4.2 failed"*, and the engineer reading it knows whose requirement
  they are about to argue with.
* **A source** — who asked. A customer, a standard, a regulation, a derivation,
  or the house. This is not metadata; it decides what may be traded away. A
  customer requirement can be renegotiated, a regulatory one cannot, and a
  derived one is only as good as the decision that produced it.
* **A rationale** — *why*. `app.design.spec.FeatureSpec` already carries a `note`
  for the same reason at the geometry level: "why is this rib here" must have an
  answer in six months, and the place an answer survives is in the artefact, not
  in a chat transcript that gets trimmed.
* **A graph** — a top-level requirement is *decomposed* into derived ones, and
  the link is explicit. Walking `parents` upward from a rib's requirement is how
  "why is this here" is answered at all; walking `children` downward is how
  "what breaks if the customer relaxes this" is answered.

**Three refusals happen at construction, not at verification.**

1. **A quantity outside the measurement vocabulary is refused** (`vocabulary.py`).
   A requirement on `mass` instead of `mass_kg` would otherwise verify as
   `UNMEASURED` forever, which is honest, silent, and indistinguishable from a
   gap nobody got round to closing.
2. **A cycle in the decomposition graph is refused** (`RequirementSet`). A cycle
   makes the "why is this here" walk non-terminating, so it is caught when the
   set is assembled rather than by whoever asks the question.
3. **A standard or a regulation with no citation is refused.** "Per ISO 898-1"
   is auditable and "because the standard says so" is not, and a signing engineer
   asks for the clause first.

**A requirement may honestly have nothing to measure — and must say what would
change that.** Duty cycle, service life, and cost are real requirements today and
none of them is measurable in this build. Forcing every requirement to carry a
measurement path would get fake paths written; allowing a silent one gets wishes.
So `measure=None` is legal exactly when `needs` says what capability would make
it checkable — the same arrangement `app.design.missions.Mission` has for a rung
it cannot build. Such a requirement is always `UNMEASURED`, always counts as
**uncovered**, and never counts as a pass.

**What satisfies a requirement is discovered, never declared.** There is no
`satisfied_by` field here on purpose. A declared list of satisfying features is a
second copy of the truth that nothing checks, and it rots the first time a
feature is renamed. The design already carries rationale per feature — the `note`
on `FeatureSpec` and on `PlannedCall` — so `app.requirements.trace` reads the
links out of those notes. Evidence is a thing that *exists in the design*, not a
claim in the requirements document.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.design.assertions import COMPARISONS, Assertion, Outcome
from app.design.errors import SpecError
from app.requirements import vocabulary
from app.requirements.errors import RequirementCycleError, RequirementError

#: Bumped when the serialised shape of a requirement set changes. Written into
#: `to_dict` so a stored document is readable by a later build that knows what
#: changed — the same promise `app.design.spec.FORMAT_VERSION` makes.
FORMAT_VERSION: Final = 1

#: A requirement id: a token that survives a file format, a URL, a report and a
#: feature note without quoting. Deliberately permissive about the *shape* —
#: `REQ-014`, `SYS-1.2.3`, `MECH_07` are all real conventions and refusing one
#: would only get it written into the statement instead — and strict about the
#: characters, because `trace.py` finds these ids inside free text and a space or
#: a comma in an id makes that impossible.
ID_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9]*[-_.][A-Za-z0-9][A-Za-z0-9._-]*$")


class Source(StrEnum):
    """Who asked for this, which decides what may be traded away.

    Not decoration. A customer requirement can be renegotiated over a phone call,
    a regulatory one cannot be negotiated at all, and a derived one is only as
    sound as the decision that produced it — so a trade study that treats the
    three alike is a trade study that proposes something illegal.
    """

    CUSTOMER = "customer"
    STANDARD = "standard"
    REGULATORY = "regulatory"
    DERIVED = "derived"
    INTERNAL = "internal"


class Status(StrEnum):
    """Where the requirement is in its own life, which is not how it verified.

    Kept apart from `Outcome` deliberately: `AGREED` says the customer signed it,
    `PASSED` says the part meets it, and conflating them makes an unbuilt design
    look verified. `OBSOLETE` is retired rather than deleted, for the reason
    `app.kernel.contract` keeps a superseded path in its table — a report, a
    drawing or a feature note that cites a removed id deserves an explanation
    rather than a silence.
    """

    DRAFT = "draft"
    AGREED = "agreed"
    OBSOLETE = "obsolete"


@dataclass(frozen=True)
class Requirement:
    """One thing the machine must do, and how anyone would know whether it does.

    The comparison vocabulary is `app.design.assertions.COMPARISONS`, unchanged
    and unextended — a requirements language that grows its own comparison
    operators immediately disagrees with the assertion layer about what `>=`
    means on a tolerance.
    """

    id: str
    statement: str

    #: The quantity constrained, in `app.requirements.vocabulary`'s terms. `None`
    #: for a requirement nothing can measure yet, which then must set `needs`.
    measure: str | None = None

    #: How the measurement is compared with the target. Required when `measure`
    #: is set; must be empty when it is not.
    comparison: str = ""

    #: The number, or an `=expression` over the design's parameters — the same
    #: late-bound bound `Assertion` already understands, so a target that tracks
    #: a design parameter travels with the design instead of silently ceasing to
    #: describe it.
    target: float | str = 0.0

    #: Slack in the unit of the measurement. Nothing converts; see the project's
    #: mm-N-MPa rule. `==` with no tolerance is refused by `Assertion` itself.
    tolerance: float = 0.0

    source: Source = Source.INTERNAL

    #: Where it came from, in words someone can look up: a clause, an RFQ
    #: section, a meeting. Required for `STANDARD` and `REGULATORY`.
    citation: str = ""

    #: Why this requirement exists at all. The answer to "can we drop it".
    rationale: str = ""

    #: Ids this requirement was decomposed from. Empty for a top-level one.
    parents: tuple[str, ...] = ()

    #: What capability would make this checkable. Required exactly when there is
    #: no `measure`, forbidden when there is — a requirement cannot be both
    #: measurable and waiting, and carrying both lets a half-specified one report
    #: as either.
    needs: str = ""

    status: Status = Status.DRAFT

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise RequirementError(
                "A requirement needs an id; it is what a report, a feature note and a "
                "drawing all cite it by."
            )
        if not ID_PATTERN.match(self.id):
            raise RequirementError(
                f"{self.id!r} is not a usable requirement id. Use a prefix, a separator "
                "and a number — REQ-014, SYS-1.2.3, MECH_07. Spaces, commas and "
                "brackets are refused because trace links are found by scanning feature "
                "notes for these ids, and an id with a space in it cannot be found there."
            )
        # Coerce the two enums from whatever the caller passed. `Source` and
        # `Status` are `StrEnum`, so `source="customer"` is a natural thing to
        # write and a natural thing for a deserialiser to hand over — and left
        # uncoerced it is silently wrong rather than loudly wrong: `active`
        # compares with `is`, so a requirement whose status is the *string*
        # "obsolete" would be counted as live in every coverage report.
        object.__setattr__(self, "source", _enum(Source, self.source, Source.INTERNAL, "source"))
        object.__setattr__(self, "status", _enum(Status, self.status, Status.DRAFT, "status"))
        if not str(self.statement).strip():
            raise RequirementError(
                f"{self.id}: needs a statement in words. The measurement is how it is "
                "checked; the statement is what was agreed, and it is what a person "
                "reads when the check fails."
            )
        if self.tolerance < 0:
            raise RequirementError(
                f"{self.id}: a negative tolerance ({self.tolerance}) would make the "
                "requirement stricter than exact, which is not a thing. Use a positive "
                "slack, or zero."
            )
        self._check_measurability()
        self._check_provenance()
        if self.id in self.parents:
            raise RequirementCycleError(
                f"{self.id} lists itself as its own parent, so 'why is this here' would "
                "never terminate. Remove it, or name the requirement it is actually "
                "derived from."
            )
        duplicated = _duplicates(self.parents)
        if duplicated:
            raise RequirementError(
                f"{self.id}: names {duplicated} as a parent more than once. Decomposition "
                "is a set of links, so a repeat says nothing and makes the count of "
                "children wrong."
            )

    def _check_measurability(self) -> None:
        if self.measure is None:
            if self.comparison:
                raise RequirementError(
                    f"{self.id}: has a comparison but nothing to measure. Give it a "
                    "measurement path, or drop the comparison and say in `needs` what "
                    "would make it checkable."
                )
            # A requirement with nothing to measure is either *waiting* on a
            # capability — which `needs` names — or *decomposed*, met through the
            # requirements it flows down into. Only the set knows which, because
            # the links point upward from the children, so the refusal for
            # "neither" lives in `RequirementSet._check_unmeasurable` rather than
            # here. Refusing at construction would mean a top-level customer
            # requirement had to claim a missing capability to be writable at all.
            return

        if str(self.needs).strip():
            raise RequirementError(
                f"{self.id}: has both a measurement and a `needs`. A requirement is "
                "checkable or it is waiting; carrying both lets a half-specified one be "
                "read as either."
            )
        if self.comparison not in COMPARISONS:
            allowed = ", ".join(sorted(COMPARISONS))
            raise RequirementError(
                f"{self.id}: {self.comparison!r} is not a comparison. Use one of: "
                f"{allowed}."
            )
        # The refusal that matters: an unmeasurable quantity is caught here, at the
        # moment somebody wrote it, and not weeks later as an UNMEASURED line that
        # reads exactly like an honest gap.
        vocabulary.require(self.measure, requirement_id=self.id)
        # And the assertion is built once here purely to be thrown away, so that
        # everything `Assertion` refuses — `==` with no tolerance on a measured
        # number being the one that bites — is refused at construction too. A
        # requirement that only fails when somebody tries to verify it is a
        # requirement that fails in front of the customer.
        self.assertion()

    def _check_provenance(self) -> None:
        if self.source in (Source.STANDARD, Source.REGULATORY) and not self.citation.strip():
            raise RequirementError(
                f"{self.id}: a {self.source} requirement must cite the clause it comes "
                "from — 'ISO 898-1 §5.2', '2006/42/EC Annex I 1.3.2'. 'The standard says "
                "so' cannot be checked, cannot be argued with, and is the first thing a "
                "signing engineer asks for."
            )
        if self.source is Source.DERIVED and not self.parents:
            raise RequirementError(
                f"{self.id}: is marked derived but names no parent, so it is derived from "
                "nothing. Name the requirement it flows down from, or set a source that "
                "says where it really came from (customer, standard, internal)."
            )

    # -- reading -------------------------------------------------------------

    @property
    def measurable(self) -> bool:
        """Whether anything in this build could check it. Not whether it passed."""
        return self.measure is not None

    @property
    def active(self) -> bool:
        """Whether it counts. An obsolete requirement is kept and not counted."""
        return self.status is not Status.OBSOLETE

    @property
    def unit(self) -> str:
        """The unit of the constrained quantity, read from the vocabulary.

        A property rather than a field, because a unit stored beside the path is
        a second copy that can disagree with the measurement contract — and the
        project rule is mm-N-MPa with nothing converting, so a disagreement is
        never resolved by scaling. Empty when there is nothing to measure.
        """
        if self.measure is None:
            return ""
        term = vocabulary.resolve(self.measure)
        return term.unit if term is not None else ""

    def assertion(self) -> Assertion:
        """The claim this requirement makes, in the vocabulary the design layer has.

        Master plan **5.2**, which the board records as blocked on this phase.
        The assertion is named after the requirement id, so every existing report
        — `AssertionReport.summary()`, a correction loop's diagnosis, a mission
        result — says "REQ-014" instead of restating an anonymous inequality.

        The statement rides in the assertion's `note`, because `AssertionResult`
        already prints a failure's note and that is the sentence a person needs
        beside a number that came out wrong.
        """
        if self.measure is None:
            raise RequirementError(
                f"{self.id}: has nothing to measure ({self.needs}), so it cannot become "
                "an assertion. Verify the set with verify_requirements, which reports it "
                "UNMEASURED with that reason rather than leaving it out."
            )
        try:
            return Assertion(
                name=self.id,
                measure=self.measure,
                comparison=self.comparison,
                bound=self.target,
                tolerance=self.tolerance,
                note=self._note(),
            )
        except SpecError as exc:
            # `Assertion` refuses `==` with no tolerance, among other things. Its
            # message is the good one; it is re-raised in this package's hierarchy
            # so a caller does not have to catch two.
            raise RequirementError(f"{self.id}: {exc}") from exc

    def _note(self) -> str:
        bits = [self.statement.strip()]
        if self.rationale.strip():
            bits.append(self.rationale.strip())
        if self.citation.strip():
            bits.append(f"({self.source}: {self.citation.strip()})")
        else:
            bits.append(f"({self.source})")
        return " ".join(bits)

    def __str__(self) -> str:
        if self.measure is None:
            return f"{self.id} [{self.status}] {self.statement} — not measurable: {self.needs}"
        unit = f" {self.unit}" if self.unit else ""
        return (
            f"{self.id} [{self.status}] {self.statement} "
            f"({self.measure} {self.comparison} {self.target}{unit})"
        )

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Canonical form: fixed key order, unset optional keys omitted."""
        out: dict[str, Any] = {"id": self.id, "statement": self.statement}
        if self.measure is not None:
            out["measure"] = self.measure
            out["comparison"] = self.comparison
            out["target"] = self.target
            if self.tolerance:
                out["tolerance"] = self.tolerance
        else:
            out["needs"] = self.needs
        out["source"] = str(self.source)
        if self.citation:
            out["citation"] = self.citation
        if self.rationale:
            out["rationale"] = self.rationale
        if self.parents:
            out["parents"] = list(self.parents)
        out["status"] = str(self.status)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Requirement:
        known = {
            "id",
            "statement",
            "measure",
            "comparison",
            "target",
            "tolerance",
            "source",
            "citation",
            "rationale",
            "parents",
            "needs",
            "status",
        }
        unknown = set(data) - known
        if unknown:
            raise RequirementError(
                f"Requirement {data.get('id')!r} carries unknown keys {sorted(unknown)}. "
                "A key this build does not understand is one it would silently drop, "
                "and a dropped requirement reads as a requirement nobody wrote."
            )
        return cls(
            id=str(data["id"]),
            statement=str(data["statement"]),
            measure=None if data.get("measure") is None else str(data["measure"]),
            comparison=str(data.get("comparison") or ""),
            target=data.get("target", 0.0),
            tolerance=float(data.get("tolerance") or 0.0),
            source=_enum(Source, data.get("source"), Source.INTERNAL, "source"),
            citation=str(data.get("citation") or ""),
            rationale=str(data.get("rationale") or ""),
            parents=tuple(str(one) for one in (data.get("parents") or ())),
            needs=str(data.get("needs") or ""),
            status=_enum(Status, data.get("status"), Status.DRAFT, "status"),
        )


@dataclass(frozen=True)
class RequirementSet:
    """Every requirement for one machine, with the decomposition graph checked.

    Frozen and validated on construction, like `DesignSpec`: a set that has been
    built is a set whose ids are unique, whose parent links all point at
    something, and whose graph has no cycle. Nothing downstream re-checks any of
    that.
    """

    name: str
    requirements: tuple[Requirement, ...] = ()

    #: Where the set came from — a filename, an RFQ, "typed in conversation".
    #: Free text, carried everywhere, read by nothing.
    origin: str = ""

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise RequirementError(
                "A requirement set needs a name; it is what a coverage report is "
                "reported under."
            )
        seen: dict[str, int] = {}
        for index, one in enumerate(self.requirements):
            if one.id in seen:
                raise RequirementError(
                    f"Two requirements are both called {one.id!r} (positions "
                    f"{seen[one.id]} and {index}). Everything here refers to a "
                    "requirement by id, so a duplicate makes every reference to it a "
                    "coin flip — including the trace link a feature note carries."
                )
            seen[one.id] = index
        self._check_parents(seen)
        self._check_acyclic()
        self._check_unmeasurable()

    def _check_parents(self, index: Mapping[str, int]) -> None:
        for one in self.requirements:
            for parent in one.parents:
                if parent not in index:
                    known = ", ".join(sorted(index)) or "none"
                    raise RequirementError(
                        f"{one.id} is decomposed from {parent!r}, which is not in this "
                        f"set. Add it, or correct the id. Requirements here: {known}."
                    )

    def _check_unmeasurable(self) -> None:
        """A requirement with nothing to measure must be waiting or decomposed.

        The third way — nothing to measure, nothing said about what would measure
        it, and nothing decomposed from it — is a wish, and it verifies as an
        honest-looking gap forever. It is refused here rather than on
        `Requirement` because the decomposition links point *upward*, so only the
        assembled set knows whether anything flows down from a given id.
        """
        with_children = {parent for one in self.requirements for parent in one.parents}
        for one in self.requirements:
            if one.measurable or str(one.needs).strip() or one.id in with_children:
                continue
            raise RequirementError(
                f"{one.id}: has no measurement, says nothing about what would make it "
                "checkable, and nothing is decomposed from it. A requirement nothing "
                "checks is a wish (master plan 11.2). Either set needs='...' naming the "
                "capability that would answer it — it is then reported UNMEASURED and "
                "counted as uncovered, which is the truth — or decompose it into "
                "requirements that name a measurement, and it will be verified through "
                "them."
            )

    def _check_acyclic(self) -> None:
        """Refuse a cycle, naming the loop.

        Iterative depth-first search with an explicit stack rather than
        recursion: a requirements document is written by people and imported
        from files, so its depth is not bounded by anything this code controls,
        and blowing the interpreter stack is a worse error message than any.
        """
        parents = {one.id: one.parents for one in self.requirements}
        finished: set[str] = set()
        for start in parents:
            if start in finished:
                continue
            path: list[str] = []
            on_path: set[str] = set()
            stack: list[tuple[str, Iterator[str]]] = [(start, iter(parents[start]))]
            path.append(start)
            on_path.add(start)
            while stack:
                node, remaining = stack[-1]
                advanced = False
                for parent in remaining:
                    if parent in on_path:
                        loop = path[path.index(parent) :] + [parent]
                        raise RequirementCycleError(
                            "The decomposition graph has a cycle: "
                            + " -> ".join(loop)
                            + ". A requirement cannot be derived from something derived "
                            "from it — 'why is this here' would never reach an answer. "
                            "Break the loop by deciding which of these is the top-level "
                            "requirement."
                        )
                    if parent not in finished:
                        stack.append((parent, iter(parents[parent])))
                        path.append(parent)
                        on_path.add(parent)
                        advanced = True
                        break
                if not advanced:
                    stack.pop()
                    finished.add(path.pop())
                    on_path.discard(node)

    # -- construction --------------------------------------------------------

    @classmethod
    def of(
        cls,
        name: str,
        requirements: Iterable[Requirement] = (),
        *,
        origin: str = "",
    ) -> RequirementSet:
        return cls(name=name, requirements=tuple(requirements), origin=origin)

    def with_requirements(self, requirements: Iterable[Requirement]) -> RequirementSet:
        """A copy with a different list. Sets are frozen; edits are copies."""
        return RequirementSet(
            name=self.name, requirements=tuple(requirements), origin=self.origin
        )

    # -- reading -------------------------------------------------------------

    def __iter__(self) -> Iterator[Requirement]:
        return iter(self.requirements)

    def __len__(self) -> int:
        return len(self.requirements)

    def __contains__(self, key: object) -> bool:
        return any(one.id == key for one in self.requirements)

    def get(self, requirement_id: str) -> Requirement:
        for one in self.requirements:
            if one.id == requirement_id:
                return one
        known = ", ".join(one.id for one in self.requirements) or "none"
        raise RequirementError(
            f"No requirement called {requirement_id!r} in {self.name!r}. In this set: "
            f"{known}."
        )

    def ids(self) -> tuple[str, ...]:
        """In the order written, not sorted — the order is how it was negotiated."""
        return tuple(one.id for one in self.requirements)

    @property
    def active(self) -> tuple[Requirement, ...]:
        """Everything not retired. What coverage is measured over."""
        return tuple(one for one in self.requirements if one.active)

    @property
    def obsolete(self) -> tuple[Requirement, ...]:
        return tuple(one for one in self.requirements if not one.active)

    def children_of(self, requirement_id: str) -> tuple[Requirement, ...]:
        """What was decomposed *from* this one — the downward half of the graph.

        Derived rather than stored, so a child added to the set cannot fail to
        appear here. The upward links are the single copy of the truth.
        """
        self.get(requirement_id)
        return tuple(one for one in self.requirements if requirement_id in one.parents)

    def ancestors_of(self, requirement_id: str) -> tuple[Requirement, ...]:
        """Every requirement this one flows down from, nearest first.

        The literal answer to "why is this here": walk up until the customer or
        the standard that started it. Terminates because the graph is checked
        acyclic on construction.
        """
        start = self.get(requirement_id)
        seen: set[str] = {start.id}
        out: list[Requirement] = []
        frontier = list(start.parents)
        while frontier:
            current = frontier.pop(0)
            if current in seen:
                continue
            seen.add(current)
            found = self.get(current)
            out.append(found)
            frontier.extend(found.parents)
        return tuple(out)

    def roots(self) -> tuple[Requirement, ...]:
        """Requirements nothing was decomposed from — the top of the flow-down."""
        return tuple(one for one in self.requirements if not one.parents)

    def leaves(self) -> tuple[Requirement, ...]:
        """Requirements nothing was decomposed *into*.

        The set that actually gets verified against geometry, most of the time:
        a top-level "the press shall weigh under 850 kg" is satisfied by its
        children being satisfied, and it is the children that name a measurement.
        """
        with_children = {parent for one in self.requirements for parent in one.parents}
        return tuple(one for one in self.requirements if one.id not in with_children)

    def assertions(self) -> tuple[Assertion, ...]:
        """Every measurable, active requirement as an assertion. Master plan 5.2.

        Silently excludes nothing: an unmeasurable requirement is not an
        assertion and cannot become one, which is why `verify_requirements`
        exists rather than callers being told to pipe this into
        `check_assertions` themselves — that path would drop exactly the
        requirements a coverage report is for.
        """
        return tuple(one.assertion() for one in self.active if one.measurable)

    def scans_needed(self) -> tuple[str, ...]:
        """Which `catia_analysis_part` analyses this set needs run before verifying.

        A measurement payload does not contain wall thickness, draft or
        continuity until somebody asks for them — `measure()` never interrogates,
        because a ray-cast scan costs thousands of kernel calls. So a set that
        constrains `minimum_wall_mm` verifies UNMEASURED against a plain
        measurement, correctly and unhelpfully, and this is how a caller learns
        what to run instead of having to know.

        Empty is the normal answer: it means nothing in the set needs more than
        the base measurement. It does **not** mean everything is measurable —
        `verify_requirements` is still the thing that says that, per requirement.
        """
        return vocabulary.scans_for(
            str(one.measure) for one in self.active if one.measurable
        )

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"format_version": FORMAT_VERSION, "name": self.name}
        if self.origin:
            out["origin"] = self.origin
        out["requirements"] = [one.to_dict() for one in self.requirements]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RequirementSet:
        unknown = set(data) - {"format_version", "name", "origin", "requirements"}
        if unknown:
            raise RequirementError(
                f"A requirement set carries unknown keys {sorted(unknown)}. A key this "
                "build does not understand is one it would silently drop."
            )
        version = int(data.get("format_version") or FORMAT_VERSION)
        if version > FORMAT_VERSION:
            raise RequirementError(
                f"This requirement set is format version {version}; this build reads up "
                f"to {FORMAT_VERSION}. Upgrade, rather than reading it partially — a "
                "requirement silently dropped for an unknown key is a requirement "
                "nobody checked."
            )
        return cls(
            name=str(data["name"]),
            requirements=tuple(
                Requirement.from_dict(one) for one in (data.get("requirements") or ())
            ),
            origin=str(data.get("origin") or ""),
        )


def _duplicates(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    twice: list[str] = []
    for value in values:
        if value in seen and value not in twice:
            twice.append(value)
        seen.add(value)
    return tuple(twice)


def _enum(kind: Any, value: Any, default: Any, field_name: str) -> Any:
    if value is None:
        return default
    try:
        return kind(str(value))
    except ValueError as exc:
        allowed = ", ".join(sorted(str(one) for one in kind))
        raise RequirementError(
            f"{value!r} is not a {field_name} this build knows. Use one of: {allowed}."
        ) from exc


# Re-exported so nothing downstream is tempted to declare a second verdict type.
# There are three outcomes in this codebase and `UNMEASURED` is never a pass.
__all__ = [
    "FORMAT_VERSION",
    "ID_PATTERN",
    "Outcome",
    "Requirement",
    "RequirementSet",
    "Source",
    "Status",
]
