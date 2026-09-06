"""The requirements model: what the machine must do, before there is any geometry.

Master plan **Phase 11**, and the phase that changes what the agent can be asked
for. *"Make me a 200×150 plate weighing 2.4 kg"* is a shape description with a
number attached. *"Make me something that carries 5 kN at a factor of safety of 2
and fits in this envelope"* is a specification, and a specification is a thing a
design can be **judged against** rather than merely produced from.

Reading order:

* `model.py` — what a requirement *is*: an assertion with a source, a rationale
  and a place in a decomposition graph. Read this first; the rest follows from it.
* `vocabulary.py` — what a requirement is allowed to constrain, and why a
  quantity nothing measures is refused when it is written rather than when it is
  checked.
* `verification.py` — a set checked against a built part: met, violated, and
  **never verified**, with coverage and per-requirement evidence (11.2, 11.4).
* `trace.py` — both directions of 11.3: why is this rib here, and what satisfies
  REQ-014.
* `parse.py` — the `.kreq` document format, and an honest list of what it does
  not understand.
* `interop.py` — export, and the recorded answer to the SysML v2 question.

**Three rules this package inherits rather than restates.**

1. **`UNMEASURED` is never a pass.** `app.design.assertions.Outcome` is reused,
   not re-declared; there is no second verdict type here. A requirement that
   could not be checked appears in the report, damages coverage, and never
   counts as met.
2. **A requirement that refers to a quantity nobody can measure is refused at
   construction.** `app.kernel.contract` prevents that defect for assertions;
   this is the same check applied one layer up, and the reason it happens at
   construction is that an unmeasurable requirement verifies as an honest-looking
   gap forever.
3. **Nothing here measures anything.** A payload arrives from `catia_measure`, an
   `OcctRunner`, or `app.design.machine_checks` — so requirements are readable,
   validatable and verifiable offline, with no seat, no kernel and no network.
   The kernel is imported lazily where it is needed at all, for the reason
   `app.design.assertions._provenance` gives.

The whole loop, end to end:

    document = parse_requirements(text, name="press frame").require()
    report = verify_requirements(document, measurements, bound_to={"plan": digest})
    print(report.summary())          # met / not met / NOT VERIFIED, with coverage
    print(trace(document, notes_from_spec(spec)).summary())   # and why each is there
"""

from app.requirements.errors import (
    RequirementCycleError,
    RequirementError,
    RequirementParseError,
    TraceError,
    VocabularyError,
)
from app.requirements.interop import (
    CONCEPT_MAP,
    EXPORTERS,
    Concept,
    Exporter,
    export,
    sysml_v2_readiness,
)
from app.requirements.model import (
    FORMAT_VERSION,
    Outcome,
    Requirement,
    RequirementSet,
    Source,
    Status,
)
from app.requirements.parse import (
    ParseProblem,
    ParseResult,
    format_requirements,
    parse_requirements,
)
from app.requirements.trace import (
    DanglingCitation,
    Note,
    TraceLink,
    TraceReport,
    notes_from_assertions,
    notes_from_plan,
    notes_from_spec,
    trace,
)
from app.requirements.verification import (
    Coverage,
    Evidence,
    RequirementReport,
    RequirementResult,
    verify_requirements,
)
from app.requirements.vocabulary import Term, catalogue

__all__ = [
    "CONCEPT_MAP",
    "EXPORTERS",
    "FORMAT_VERSION",
    "Concept",
    "Coverage",
    "DanglingCitation",
    "Evidence",
    "Exporter",
    "Note",
    "Outcome",
    "ParseProblem",
    "ParseResult",
    "Requirement",
    "RequirementCycleError",
    "RequirementError",
    "RequirementParseError",
    "RequirementReport",
    "RequirementResult",
    "RequirementSet",
    "Source",
    "Status",
    "Term",
    "TraceError",
    "TraceLink",
    "TraceReport",
    "VocabularyError",
    "catalogue",
    "export",
    "format_requirements",
    "notes_from_assertions",
    "notes_from_plan",
    "notes_from_spec",
    "parse_requirements",
    "sysml_v2_readiness",
    "trace",
    "verify_requirements",
]
