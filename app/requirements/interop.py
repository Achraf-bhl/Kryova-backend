"""Exporting a requirement set, and the SysML v2 question — answered, with a date.

The master plan's technology register names **SysML v2** via **SysON** and
**Capella** for requirements, on the grounds that 2026 is its tooling maturity
year. Phase 11 asks whether it has a serialisation worth targeting *now*. This
module is the answer, the reasoning, and the boundary that makes the answer
reversible.

## The finding, as of 2026-09: it has one, and adopting it now would be premature

**There is a real serialisation.** SysML v2 is not only a textual notation: the
OMG *Systems Modeling API and Services* specification defines a JSON payload for
model elements, and that is what SysON and the reference implementation exchange.
The concepts a requirements model needs are all present and map cleanly onto what
is in `model.py`:

| Kryova | SysML v2 |
|---|---|
| `Requirement` | `RequirementUsage` (of a `RequirementDefinition`) |
| `statement` | the usage's documentation |
| `measure` + `comparison` + `target` | a `ConstraintUsage` in a `require` membership |
| `parents` | nested requirement usages / subsetting |
| a satisfying feature | `SatisfyRequirementUsage` |
| verification | `VerificationCase` and a `verify` membership |
| `source`, `citation` | metadata annotation; there is no first-class field |

So the mapping is not the obstacle. **Three other things are**, and each is the
kind of problem that shows up as a file a real tool silently misreads:

1. **The payload is a KerML element graph, not a document.** Every element needs
   a UUID, a `@type`, and correctly-typed membership relationships to its
   owners — a requirement usage is five or six elements, not one. Emitting that
   from a nine-field dataclass is straightforward to write and impossible to
   *validate* here: nothing in this repository can tell a well-formed payload
   from one SysON will open with the constraints quietly missing. A file that a
   tool accepts and reads wrongly is the worst outcome available, and it is the
   likely one.
2. **The obligation is round-trip, and we have no import.** An interchange format
   nobody reads back is a report with extra ceremony. Import means parsing KerML
   memberships, which is a much larger job than this phase, and half an
   interchange is not interchange.
3. **Capella is not SysML v2 at all.** It implements Arcadia, with a bridge. So
   "aligned to SysML v2" does not get Capella for free, and choosing the format
   before there is a customer asking for a specific tool is choosing without the
   information that decides it.

**What we do instead.** The requirement set's own JSON (`RequirementSet.to_dict`)
and the `.kreq` text form (`parse.format_requirements`) are complete, versioned,
round-trippable and readable by a person — and `.kreq` is the one an engineer can
actually edit. Those carry the phase.

**What makes it addable.** `Exporter` below is the seam. It is a Protocol with
one method, deliberately the same shape as `app.solve.Solver` and
`app.jobs.JobQueue`: a SysML v2 exporter drops in without `model.py`,
`verification.py` or anything above them knowing which one ran. `CONCEPT_MAP` is
the mapping table above **as data**, and
`tests/test_requirements_interop.py` asserts that every field of `Requirement`
appears in it or is listed as deliberately internal — so the day someone writes
the exporter, the fields that have no SysML home are already enumerated rather
than discovered one at a time. `sysml_v2_readiness()` states what is still
missing, in words, so this decision is re-openable by reading it rather than by
re-doing the research.

**No SysML emitter is shipped here, not even a partial one.** A half-correct
export labelled "starting point" is a file somebody sends to a customer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from app.requirements.model import Requirement, RequirementSet
from app.requirements.parse import format_requirements


@runtime_checkable
class Exporter(Protocol):
    """One serialisation of a requirement set.

    The seam. One method, like every other seam in this codebase, so adding
    SysML v2 later is a new class and a registry entry rather than an edit to the
    model.
    """

    # Read-only properties rather than plain attributes, and the difference is
    # not cosmetic: a Protocol declaring a *mutable* attribute is invariant in
    # it, so a class setting `name: str = "kreq"` does not satisfy it and mypy
    # refuses the registry below. Declaring them read-only says what is actually
    # required — an exporter must be able to tell you its name, not let you
    # change it.

    @property
    def name(self) -> str:
        """Short, stable, and what a caller asks for by name."""
        ...

    @property
    def suffix(self) -> str:
        """Conventional file extension, including the dot."""
        ...

    def export(self, requirements: RequirementSet) -> str:
        """The whole set as text. Must not lose a requirement, ever."""
        ...


@dataclass(frozen=True)
class Concept:
    """One row of the Kryova -> SysML v2 mapping, kept as data rather than prose.

    `sysml` is empty for a field SysML v2 has no first-class home for. That is
    not a gap to be papered over with a custom annotation without saying so — it
    is exactly the information a future exporter's author needs on day one.
    """

    field: str
    sysml: str
    note: str


#: The mapping, researched 2026-09. Every field of `Requirement` appears here or
#: in `INTERNAL_FIELDS`; a test asserts it, so a field added to the model cannot
#: quietly acquire no interop story.
CONCEPT_MAP: Final[tuple[Concept, ...]] = (
    Concept(
        field="id",
        sysml="RequirementUsage.declaredShortName",
        note="SysML's short name is the human id; the element also carries a UUID we "
        "do not have and would have to mint and store.",
    ),
    Concept(
        field="statement",
        sysml="Documentation / doc comment on the usage",
        note="Free text in both. The one field that maps with nothing lost.",
    ),
    Concept(
        field="measure",
        sysml="ConstraintUsage operand (an attribute reference)",
        note="SysML would resolve this against a model of the subject's attributes; "
        "we resolve it against app.kernel.contract. The two vocabularies are not the "
        "same and a real export must state which it means.",
    ),
    Concept(
        field="comparison",
        sysml="the operator inside the ConstraintUsage expression",
        note="Same six operators. No conversion needed.",
    ),
    Concept(
        field="target",
        sysml="the literal or feature reference on the right of the constraint",
        note="An '=expression' target has no SysML equivalent short of exporting the "
        "design's parameters as features too — out of scope for a requirements export.",
    ),
    Concept(
        field="tolerance",
        sysml="widened bounds on the constraint expression",
        note="SysML has no tolerance concept; it would become a second inequality, "
        "which changes what the document says and must be a deliberate choice.",
    ),
    Concept(
        field="parents",
        sysml="nested RequirementUsage, or subsetting",
        note="SysML's decomposition is containment-flavoured; ours is a DAG and a "
        "requirement may have several parents. Containment cannot express that, so an "
        "exporter must use subsetting and accept that some tools render it less well.",
    ),
    Concept(
        field="source",
        sysml="",
        note="No first-class field. Customer / standard / regulatory / derived would "
        "become metadata, and metadata is where interoperability quietly stops.",
    ),
    Concept(
        field="citation",
        sysml="",
        note="Same: metadata, or a doc comment convention. Losing it is not an option "
        "— it is what a signing engineer reads first.",
    ),
    Concept(
        field="rationale",
        sysml="Documentation, second doc comment",
        note="Exportable, but SysML tools show one documentation block, so rationale "
        "and statement tend to merge on import.",
    ),
    Concept(
        field="needs",
        sysml="",
        note="No equivalent. SysML has no notion of 'this requirement is not "
        "verifiable in this build', which is one of the more useful things this model "
        "records.",
    ),
    Concept(
        field="status",
        sysml="",
        note="Tool-specific. SysON and Capella each have their own lifecycle attribute.",
    ),
)

#: Fields with no interop story on purpose. Empty today; kept so a future field
#: can be excluded *explicitly* rather than by being forgotten.
INTERNAL_FIELDS: Final[frozenset[str]] = frozenset()


def sysml_v2_readiness() -> tuple[str, ...]:
    """What would have to be true before a SysML v2 export could be shipped.

    Kept as a function rather than a comment so it can be printed, tested, and
    read by whoever re-opens the decision — which is the point of recording a
    decision at all.
    """
    return (
        "A KerML element-graph emitter: a requirement usage is five or six elements "
        "with UUIDs and typed memberships, not one object.",
        "A validator, or a round-trip against SysON, so a payload a tool misreads is "
        "caught here rather than in front of a customer.",
        "An importer. Export with no import is a report, not interchange.",
        "A decision about the fields SysML has no home for — source, citation, needs, "
        "status — because dropping them silently loses the audit trail this model "
        "exists for.",
        "A customer or a standard naming the tool. Capella is Arcadia, not SysML v2, "
        "so 'aligned to SysML v2' does not decide which of the two is meant.",
    )


@dataclass(frozen=True)
class JsonExporter:
    """The set as JSON — complete, versioned, and read back by `from_dict`.

    The default because it is the only one this build can promise round-trips.
    """

    name: str = "json"
    suffix: str = ".json"

    def export(self, requirements: RequirementSet) -> str:
        return json.dumps(
            requirements.to_dict(), ensure_ascii=False, indent=2, sort_keys=False
        )


@dataclass(frozen=True)
class KreqExporter:
    """The set as the `.kreq` document an engineer can edit and hand back.

    The one an engineer wants. Round-trips through `parse_requirements`, which a
    test asserts — a writer that cannot be read back is a second format nobody
    maintains.
    """

    name: str = "kreq"
    suffix: str = ".kreq"

    def export(self, requirements: RequirementSet) -> str:
        return format_requirements(requirements)


#: Everything this build can write. A SysML v2 exporter joins this mapping and
#: nothing above it changes — that is what makes the decision above reversible
#: rather than merely deferred.
EXPORTERS: Final[Mapping[str, Exporter]] = {
    JsonExporter.name: JsonExporter(),
    KreqExporter.name: KreqExporter(),
}


def export(requirements: RequirementSet, fmt: str = "kreq") -> str:
    """Write a requirement set in one of the formats this build actually supports.

    Refuses an unknown format by name rather than falling back to a default: a
    caller who asked for SysML and silently received JSON would ship JSON to
    somebody expecting SysML.
    """
    exporter = EXPORTERS.get(fmt)
    if exporter is None:
        available = ", ".join(sorted(EXPORTERS))
        missing = "; ".join(sysml_v2_readiness())
        extra = (
            f" SysML v2 is not one of them yet — see app.requirements.interop for why: "
            f"{missing}"
            if fmt.lower().startswith("sysml")
            else ""
        )
        raise ValueError(
            f"No exporter called {fmt!r}. This build writes: {available}.{extra}"
        )
    return exporter.export(requirements)


def concept_for(field: str) -> Concept | None:
    for one in CONCEPT_MAP:
        if one.field == field:
            return one
    return None


def mapped_fields() -> frozenset[str]:
    """Every `Requirement` field the concept map or the internal list accounts for."""
    return frozenset(one.field for one in CONCEPT_MAP) | INTERNAL_FIELDS


def requirement_fields() -> frozenset[str]:
    """The model's own field names, read from the dataclass rather than listed here."""
    annotations: dict[str, Any] = {}
    for base in reversed(Requirement.__mro__):
        annotations.update(getattr(base, "__annotations__", {}))
    return frozenset(annotations)


__all__ = [
    "CONCEPT_MAP",
    "EXPORTERS",
    "INTERNAL_FIELDS",
    "Concept",
    "Exporter",
    "JsonExporter",
    "KreqExporter",
    "concept_for",
    "export",
    "mapped_fields",
    "requirement_fields",
    "sysml_v2_readiness",
]
