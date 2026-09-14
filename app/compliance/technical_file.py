"""The design record as Kryova's contribution to a technical file — master plan
E19 task 3, the half that knows the law and touches no database.

Annex IV, Part A of Regulation (EU) 2023/1230 lists fifteen elements, (a) to
(o), that the technical documentation for machinery "shall include at least".
All fifteen were read from the Official Journal and are quoted below, so the
export is organised by the clause a market surveillance authority will read it
against rather than by how Kryova happens to store things.

**No element is marked as supplied by Kryova in full**, and that is not
modesty. The ones Kryova contributes to — the description, the drawings'
source, the explanations, the design calculations — each have a part only the
manufacturer can write: the intended use, the drawings themselves as issued,
the tests and inspections. Eleven of the fifteen are the manufacturer's alone,
and the file says so element by element instead of leaving them out, because
a technical file that listed only what Kryova had would read as complete.

**Point (m) is answered, not skipped.** It asks for the source code or
programming logic of safety related software. Kryova produces none
(`boundary.Stance.NEVER`), and the design specification in this file is not
that software — it is what the geometry is compiled from.

**Integrity, not signature.** Every artefact carries the SHA-256 of its
canonical JSON, and the file carries a digest over everything else in it, so a
reader can tell whether what they hold is what was exported. Nobody signs it:
a digest says the bytes did not change, and says nothing about who stands
behind them.

**The format half of "still opens in ten years", not the storage half.**
Article 10(3) keeps the documentation for at least ten years. This file is
UTF-8 JSON with a format version and no binary content, which any reader in ten
years can open; keeping it for ten years is a retention question (E15) that
nothing in this product answers yet, and `NOT_INCLUDED` says so in the file.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.compliance.eu_machinery_regulation import OJ_L_165
from app.compliance.provisions import QuotedClause

FORMAT: Final = "kryova-technical-file-contribution"
FORMAT_VERSION: Final = 1


def _clause(citation: str, quote: str, note: str = "") -> QuotedClause:
    return QuotedClause(citation=citation, quote=quote, sources=(OJ_L_165,), note=note)


ANNEX_IV_A: Final = _clause(
    "Annex IV, Part A",
    "The technical documentation shall specify the means used by the manufacturer to ensure the "
    "conformity of the machinery or related product with the applicable essential health and "
    "safety requirements set out in Annex III. The technical documentation shall include at least "
    "the following elements:",
)
ARTICLE_10_3: Final = _clause(
    "Article 10(3)",
    "Manufacturers shall keep the technical documentation and the EU declaration of conformity at "
    "the disposal of the market surveillance authorities for at least 10 years after the "
    "machinery or the related product has been placed on the market or put into service. Where "
    "relevant, the source code or the programming logic included in the technical documentation "
    "shall, upon a reasoned request, be made available to the competent national authorities, if "
    "that source code or programming logic is necessary in order for them to be able to check "
    "compliance with the essential health and safety requirements set out in Annex III.",
)

_POINTS: Final[dict[str, str]] = {
    "a": "a complete description of the machinery or related product and of its intended use;",
    "b": (
        "the documentation on risk assessment demonstrating the procedure carried out, including: "
        "(i) a list of the essential health and safety requirements that are applicable to the "
        "machinery or related product; (ii) the description of the protective measures implemented "
        "to meet each applicable essential health and safety requirement and, when appropriate, "
        "the indication of the residual risks associated with the machinery or related product;"
    ),
    "c": (
        "design and manufacturing drawings and schemes of the machinery or related product and of "
        "its components, sub-assemblies and circuits;"
    ),
    "d": (
        "the descriptions and explanations necessary for the understanding of the drawings and "
        "schemes referred to in point (c) and of the operation of the machinery or related product;"
    ),
    "e": (
        "the references of the harmonised standards referred to in Article 20(1) or common "
        "specifications adopted by the Commission in accordance with Article 20(3) that have been "
        "applied for the design and manufacture of the machinery or related product. In the event "
        "of partial application of harmonised standards or common specifications, the "
        "documentation shall specify the parts, which have been applied;"
    ),
    "f": (
        "where harmonised standards or common specifications have not been applied or have been "
        "only partially applied, descriptions of the other technical specifications that have been "
        "applied in order to meet each applicable essential health and safety requirement;"
    ),
    "g": (
        "reports and/or results of the design calculations, tests, inspections and examinations "
        "carried out to verify the conformity of the machinery or related product with the "
        "applicable essential health and safety requirements;"
    ),
    "h": (
        "a description of the means used by the manufacturer during the production of the "
        "machinery or related product to ensure the conformity of the machinery or related product "
        "produced with the design specifications;"
    ),
    "i": "a copy of the instructions for use and the information set out in section 1.7.4 of Annex III;",
    "j": (
        "where appropriate, the EU declaration of incorporation for partly completed machinery set "
        "out in Annex V, Part B, and the assembly instructions set out in Annex XI;"
    ),
    "k": (
        "where appropriate, copies of the EU declarations of conformity of machinery or related "
        "products as well as any product covered by other Union harmonisation legislation "
        "incorporated into the machinery or related product;"
    ),
    "l": (
        "for machinery or related products produced in series, the internal measures that will be "
        "implemented to ensure that the machinery or related product remains in conformity with "
        "this Regulation;"
    ),
    "m": (
        "the source code or programming logic of the safety related software to demonstrate the "
        "conformity of the machinery or related product with this Regulation further to a reasoned "
        "request from a competent national authority provided that is necessary in order for those "
        "authorities to be able to check compliance with the essential health and safety "
        "requirements set out in Annex III;"
    ),
    "n": (
        "for sensor-fed, remotely-driven, or autonomous machinery or related products, if the "
        "safety related operations are controlled by sensor data, a description, where "
        "appropriate, of the general characteristics, capabilities and limitations of the system, "
        "data, development, testing and validation processes used;"
    ),
    "o": (
        "the results of research and tests on components, fittings or the machinery or related "
        "product carried out by the manufacturer to determine whether by its design or "
        "construction it is capable of being assembled and put into service safely."
    ),
}

POINTS: Final[dict[str, QuotedClause]] = {
    letter: _clause(f"Annex IV, Part A, point ({letter})", words) for letter, words in _POINTS.items()
}


class Contribution(StrEnum):
    """How much of one element this file carries."""

    #: Kryova supplies part of it, named; the rest is the manufacturer's.
    PART = "part"
    #: The manufacturer's alone. Listed so the gap is visible, not omitted.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Element:
    letter: str
    contribution: Contribution
    #: What this file carries towards it, or why it carries nothing.
    kryova: str
    #: What only the manufacturer can supply.
    manufacturer: str
    #: Ids of the artefacts in this file that serve it.
    artefacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.contribution is Contribution.PART) != bool(self.artefacts):
            raise ValueError(
                f"Point ({self.letter}): an element Kryova contributes to names the artefacts that "
                "carry the contribution, and one it does not names none."
            )

    @property
    def clause(self) -> QuotedClause:
        return POINTS[self.letter]


#: Artefact ids. Stable strings, because a reader's notes will cite them.
SPECIFICATION: Final = "design-specification"
BUILD_PLAN: Final = "build-plan"
REVISIONS: Final = "revision-history"
OPERATIONS: Final = "operation-log"
ANALYSES: Final = "analyses"
APPROVALS: Final = "approval-gates"

ELEMENTS: Final[tuple[Element, ...]] = (
    Element(
        "a",
        Contribution.PART,
        kryova="The design specification's name, description and parameters.",
        manufacturer="The intended use, and the description of the machine as a product.",
        artefacts=(SPECIFICATION,),
    ),
    Element(
        "b",
        Contribution.NONE,
        kryova=(
            "Nothing. A risk assessment is only ever a draft Kryova prepares for a named person "
            "(`app/compliance/boundary.py`), and no draft is in this file."
        ),
        manufacturer="The risk assessment, its applicable requirements and protective measures.",
    ),
    Element(
        "c",
        Contribution.PART,
        kryova=(
            "The specification the geometry is compiled from and the resolved build plan. Drawings "
            "and STEP files are generated from these by a build and are not stored with the record, "
            "so they are not in this file."
        ),
        manufacturer="The drawings and schemes as issued, including circuits and bought-in parts.",
        artefacts=(SPECIFICATION, BUILD_PLAN),
    ),
    Element(
        "d",
        Contribution.PART,
        kryova=(
            "Each feature's rationale note, the revision history with who changed what and when, "
            "and the log of operations that built the part."
        ),
        manufacturer="How the machine operates.",
        artefacts=(SPECIFICATION, REVISIONS, OPERATIONS),
    ),
    Element(
        "e",
        Contribution.NONE,
        kryova="Nothing. Which harmonised standards were applied is the manufacturer's decision.",
        manufacturer="The references of the harmonised standards or common specifications applied.",
    ),
    Element(
        "f",
        Contribution.NONE,
        kryova="Nothing.",
        manufacturer="The other technical specifications applied, requirement by requirement.",
    ),
    Element(
        "g",
        Contribution.PART,
        kryova=(
            "The analyses run in the design's project, each with its solver, version, case, mesh "
            "and convergence basis, and the statement that none has been validated against a "
            "physical part. Sign-off decisions recorded against the conversation are included "
            "beside them."
        ),
        manufacturer=(
            "Which of those results the conformity rests on, and every test, inspection and "
            "examination carried out on the machine."
        ),
        artefacts=(ANALYSES, APPROVALS),
    ),
    Element(
        "h",
        Contribution.NONE,
        kryova="Nothing.",
        manufacturer="The means used during production to keep it to the design specifications.",
    ),
    Element(
        "i",
        Contribution.NONE,
        kryova=(
            "Nothing yet. Instructions are a draft for a named person, and their digital delivery "
            "must pass `app/compliance/instructions.py`."
        ),
        manufacturer="The instructions for use and the Annex III section 1.7.4 information.",
    ),
    Element("j", Contribution.NONE, kryova="Nothing.", manufacturer="Where appropriate, the declaration of incorporation and assembly instructions."),
    Element(
        "k",
        Contribution.NONE,
        kryova="Nothing. Bought-in components are integrated, and their declarations are their suppliers'.",
        manufacturer="Copies of the declarations of conformity of what is incorporated.",
    ),
    Element("l", Contribution.NONE, kryova="Nothing.", manufacturer="The internal measures for series production."),
    Element(
        "m",
        Contribution.NONE,
        kryova=(
            "Nothing, because Kryova produces no safety related software for the machine. The "
            "design specification in this file is what the geometry is compiled from; it is not "
            "software that runs on the machine."
        ),
        manufacturer="On a reasoned request, the source code or logic of any safety related software.",
    ),
    Element("n", Contribution.NONE, kryova="Nothing.", manufacturer="For sensor-fed, remotely-driven or autonomous machinery, the description the point asks for."),
    Element("o", Contribution.NONE, kryova="Nothing.", manufacturer="The results of research and tests on components, fittings or the machine."),
)

#: What a reader might expect here and will not find, with the reason.
NOT_INCLUDED: Final[tuple[str, ...]] = (
    "Drawings and STEP geometry: generated from the specification by a build and not stored with "
    "the design record.",
    "Requirement coverage: computed on request from a specification the client supplies, and not "
    "stored, so there is no record of it to export.",
    "Ten-year retention: Article 10(3) requires the documentation to be kept for at least ten "
    "years, and nothing in Kryova keeps an export for that long. Keeping this file is the "
    "manufacturer's.",
)


def canonical(value: Any) -> bytes:
    """The bytes a digest is taken over: sorted keys, no whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class Artefact:
    id: str
    description: str
    content: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "media_type": "application/json",
            "sha256": sha256_of(self.content),
            "content": self.content,
        }


def assemble(
    *,
    design: dict[str, Any],
    artefacts: list[Artefact],
    statements: dict[str, str],
    generated_at: datetime.datetime,
) -> dict[str, Any]:
    """The file. Refuses an element pointing at an artefact that is not in it."""
    held = {artefact.id for artefact in artefacts}
    for element in ELEMENTS:
        missing = set(element.artefacts) - held
        if missing:
            raise ValueError(
                f"Point ({element.letter}) cites {sorted(missing)}, which this file does not carry."
            )
    body: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "generated_at": generated_at.isoformat(),
        "what_this_is": (
            "Kryova's contribution to the technical documentation for one machine design, organised "
            "by Annex IV, Part A of Regulation (EU) 2023/1230. It is not the technical "
            "documentation: most of that is the manufacturer's, and each element below says which "
            "part."
        ),
        "regulation": {
            "act": OJ_L_165.document,
            "url": OJ_L_165.url,
            "read_on": OJ_L_165.read_on.isoformat(),
            "annex": ANNEX_IV_A.quote,
            "retention": {"citation": ARTICLE_10_3.citation, "quote": ARTICLE_10_3.quote},
        },
        "statements": statements,
        "design": design,
        "elements": [
            {
                "point": element.letter,
                "citation": element.clause.citation,
                "quote": element.clause.quote,
                "contribution": element.contribution.value,
                "kryova": element.kryova,
                "manufacturer": element.manufacturer,
                "artefacts": list(element.artefacts),
            }
            for element in ELEMENTS
        ],
        "artefacts": [artefact.to_dict() for artefact in artefacts],
        "not_included": list(NOT_INCLUDED),
    }
    body["digest"] = {"algorithm": "sha256", "over": "this object without 'digest', canonical JSON", "value": sha256_of(body)}
    return body


def verify(file: dict[str, Any]) -> bool:
    """Whether a file's digest and every artefact hash still match its contents."""
    body = {key: value for key, value in file.items() if key != "digest"}
    if file.get("digest", {}).get("value") != sha256_of(body):
        return False
    return all(item["sha256"] == sha256_of(item["content"]) for item in file.get("artefacts", []))


__all__ = [
    "ANALYSES",
    "APPROVALS",
    "ARTICLE_10_3",
    "BUILD_PLAN",
    "ELEMENTS",
    "FORMAT",
    "FORMAT_VERSION",
    "NOT_INCLUDED",
    "OPERATIONS",
    "POINTS",
    "REVISIONS",
    "SPECIFICATION",
    "Artefact",
    "Contribution",
    "Element",
    "assemble",
    "canonical",
    "sha256_of",
    "verify",
]
