"""Which ASME document is behind each word this package uses. Master plan 20.1.

`app.verify` has said "the ASME V&V 20 split" in three places since it was
written, for the general distinction between *verification* (are the equations
being solved correctly) and *validation* (are they the right equations). That
citation is imprecise in a way that matters once a reader can click through to
it: **V&V 20-2009 is scoped to fluids and heat transfer**, and every benchmark
this codebase actually runs today — LE1, LE3, LE10, LE11, FV52 — is solid
mechanics. The document that owns solid-mechanics V&V is **V&V 10**, and the
document that defines the *terms* "verification", "validation" and
"uncertainty quantification" themselves, independent of discipline, is
**VVUQ 1**. Citing V&V 20 for a claim about a plate under pressure borrows the
right *shape* of argument from the wrong committee's document — plausible, and
checkable, and wrong once checked.

**This module does not decide anything new.** It names, once, which document
backs which word, so `app/verify/__init__.py`, `benchmarks.py` and
`register.py` can cite it instead of each repeating a slightly different
paraphrase that drifts the day one of them is edited — the same argument
`nafems.SOURCES` makes for a citation repeated inline.

## The portfolio, and where each piece actually applies here

* **VVUQ 1-2022** — *Standard Terminology for Verification, Validation, and
  Uncertainty Quantification in Computational Modeling and Simulation.* The
  source for the words themselves. Cited wherever this package draws the
  verification/validation line in the abstract, discipline-unspecified.
* **V&V 10-2019** — *Standard for Verification and Validation in Computational
  Solid Mechanics.* The portfolio that actually governs every benchmark
  `app.verify.nafems` currently encodes: elastic membranes, a hemisphere under
  point loads, a thick plate, a tapered cylinder under a temperature field, a
  vibrating plate. Cited wherever a claim is about *this codebase's own solid-
  mechanics benchmark suite* specifically.
* **V&V 10.1-2012** — the worked illustration accompanying V&V 10. Cited only
  where a specific worked example is the point, not as a second citation for
  the standard itself.
* **VVUQ 10.2-2021** — the role of uncertainty quantification in solid
  mechanics V&V. Not applied anywhere in this codebase yet: nothing here
  performs UQ (E20 task 1's own deliverable is the vocabulary, not a UQ
  capability), so this entry exists to be cited honestly *when* one is added,
  not before.
* **V&V 20-2009** — *Standard for Verification and Validation in Computational
  Fluid Dynamics and Heat Transfer.* Fifteen years old and still the base
  document for the field. **Its Grid Convergence Index / Richardson
  extrapolation procedure (Celik et al. 2008) is what `app.verify.convergence`
  actually implements**, and that citation is unchanged by this module — GCI is
  this committee's own method and is used here as a numerical-error estimator
  regardless of which physics produced the mesh, the same way a statistical
  test from one field is legitimately borrowed by another. What V&V 20 must
  **not** be cited for is the general verification/validation *terminology* —
  that is VVUQ 1's job — nor, until `app/solve/conduction.py` gets a fluids
  or CFD sibling, as this codebase's own domain standard.
* **VVUQ 20.1-2024** — extends V&V 20. Not applied here for the same reason
  VVUQ 10.2 is not: no CFD capability exists yet to hold it to.

## Two documents that must never be cited as ours

* **V&V 40-2018** — *Assessing Credibility of Computational Modeling through
  Verification and Validation: Application to Medical Devices.* Scoped to
  medical devices by its own title. It is the natural document a search for
  "risk-informed credibility framework" returns, and citing it for a stamping
  press or a gearbox would be borrowing another industry's standard — the
  exact mistake E20 task 1 exists to prevent. `NOT_OURS` says so in one place
  so nobody reaches for it a second time having forgotten why the first reach
  was wrong.
* **VVUQ 70** — the ASME subcommittee for verification and validation of
  machine-learning models. **Has no published standard**, as of the research
  this module was written from. There is therefore no ASME-normative basis
  for validating an ML surrogate, which is the ceiling `E10 task 4` (the
  surrogate flywheel) and any future ML-based result may claim against: none,
  until this changes. `NO_STANDARD` records the absence rather than silence,
  because a subcommittee that publishes nothing is a fact worth stating, not
  a gap to work around by citing something adjacent.

## The words themselves, and what this codebase's evidence is (master plan 20.3)

`DEFINITIONS` quotes the terms rather than paraphrasing them, each with the
document it was read from. **None is read off an ASME standard directly** —
those are sold, not published — so each is quoted from ASME's own public page
or from a Sandia presentation by a V&V 20 committee member quoting V&V 10 and
V&V 20. That is second-hand, it says so in `SOURCES`, and it is still a
document a reader can open, which a remembered definition is not.

What those definitions settle, and nothing here needed an opinion to settle it:
**every piece of evidence Kryova holds is verification.** A closed-form
identity and a published NAFEMS reference solution are both answers to a
stated *mathematical* problem, so agreeing with either is code verification;
a convergence study estimates the numerical error of one calculation, which
is solution verification. Validation compares a simulation result against
*experimental data* — the Sandia procedure writes it as `E = S − D` — and no
Kryova result has ever been compared against a measurement of a physical part.
`KRYOVA_EVIDENCE[EvidenceKind.VALIDATION]` is therefore empty, `NOT_VALIDATED`
is the sentence every surface that shows a number carries, and the check at the
bottom of this module ties the two together: the day validation evidence is
recorded, import fails until somebody rewrites the sentence, rather than the
product going on saying something that stopped being true.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: One citation per document actually behind a word this package uses. Keyed
#: on a short, stable id rather than the document number alone, because
#: `V&V 10` and `V&V 10.1` differ by a dot a call site could drop.
STANDARDS: Final[dict[str, str]] = {
    "vvuq-1": (
        "ASME VVUQ 1-2022, 'Standard Terminology for Verification, Validation, "
        "and Uncertainty Quantification in Computational Modeling and "
        "Simulation'. The source for what 'verification', 'validation' and "
        "'uncertainty quantification' mean, independent of discipline."
    ),
    "vv-10": (
        "ASME V&V 10-2019, 'Standard for Verification and Validation in "
        "Computational Solid Mechanics'. Governs every benchmark "
        "app.verify.nafems currently encodes (LE1, LE3, LE10, LE11, FV52), "
        "all of which are solid mechanics."
    ),
    "vv-10.1": (
        "ASME V&V 10.1-2012, the worked illustration accompanying V&V 10-2019."
    ),
    "vvuq-10.2": (
        "ASME VVUQ 10.2-2021, the role of uncertainty quantification in "
        "solid-mechanics V&V. Not applied in this codebase yet — nothing here "
        "performs UQ."
    ),
    "vv-20": (
        "ASME V&V 20-2009, 'Standard for Verification and Validation in "
        "Computational Fluid Dynamics and Heat Transfer'. Its Grid Convergence "
        "Index procedure (Celik et al. 2008) is what app.verify.convergence "
        "implements; it is not the source for this package's general "
        "verification/validation terminology, and is not this codebase's own "
        "domain standard until a CFD capability exists."
    ),
    "vvuq-20.1": (
        "ASME VVUQ 20.1-2024, extending V&V 20-2009. Not applied in this "
        "codebase yet — no CFD capability exists to hold it to."
    ),
}

#: A document that must never be cited as this codebase's credibility
#: framework, with the reason in the value rather than left to be
#: rediscovered by whoever reaches for it next.
NOT_OURS: Final[dict[str, str]] = {
    "vv-40": (
        "ASME V&V 40-2018, 'Assessing Credibility of Computational Modeling "
        "through Verification and Validation: Application to Medical "
        "Devices'. Scoped to medical devices by its own title. Citing it here "
        "would borrow another industry's standard."
    ),
}

#: A subcommittee named as though it settles something, and does not.
NO_STANDARD: Final[dict[str, str]] = {
    "vvuq-70": (
        "ASME VVUQ 70, verification and validation of machine-learning "
        "models. Has no published standard as of the research this module "
        "was written from. There is therefore no ASME-normative basis for "
        "validating an ML surrogate — the ceiling on any claim E10 task 4 "
        "(the surrogate flywheel) or a future ML-based result may make."
    ),
}


#: The document backing the general, discipline-unspecified split this
#: package draws everywhere between verification and validation. Exists so a
#: docstring can write `f"the {TERMINOLOGY_SOURCE} split"` — no, in practice a
#: docstring quotes `STANDARDS["vvuq-1"]` directly — and, more usefully, so a
#: test can assert every module that draws the split names this id rather
#: than a paraphrase of V&V 20.
TERMINOLOGY_SOURCE: Final = "vvuq-1"

#: The document backing a claim about *this codebase's own* benchmark suite,
#: because every case in it is solid mechanics.
SOLID_MECHANICS_SOURCE: Final = "vv-10"


#: The documents the definitions below were actually read from, on the date
#: they were read. Written once each, the `nafems.SOURCES` argument again.
SOURCES: Final[dict[str, str]] = {
    "asme-vvuq-page": (
        "ASME, 'Verification, Validation and Uncertainty Quantification (VVUQ)', "
        "https://www.asme.org/codes-standards/publications-information/"
        "verification-validation-uncertainty — ASME's own public summary of its "
        "VVUQ portfolio. Read 2026-09-14."
    ),
    "sandia-sand2016-5342c": (
        "K. Dowding (Sandia National Laboratories, member of the ASME V&V 20 "
        "committee), 'Overview of ASME V&V 20-2009 Standard for Verification and "
        "Validation in Computational Fluid Mechanics and Heat Transfer', "
        "SAND2016-5342C, presented at the 2016 Inverse Problems Symposium, "
        "https://www.osti.gov/servlets/purl/1368927, slides 5-8. Quotes V&V 10 and "
        "V&V 20 and attributes each definition; second-hand for the standards "
        "themselves, which were not read. Read 2026-09-14."
    ),
}


@dataclass(frozen=True, slots=True)
class Definition:
    """One term, quoted, with where the quotation was read."""

    term: str
    text: str
    #: The standard the quotation is attributed to by the document it was read in.
    attributed_to: str
    source: str

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.source.strip():
            raise ValueError(
                f"A definition of {self.term!r} with no text or no source is a "
                "paraphrase from memory, which is what this module exists to stop."
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "term": self.term,
            "text": self.text,
            "attributed_to": self.attributed_to,
            "source": self.source,
        }


DEFINITIONS: Final[dict[str, Definition]] = {
    "verification": Definition(
        term="verification",
        text=(
            "The process of determining that a computational model accurately "
            "represents the underlying mathematical model and its solution."
        ),
        attributed_to="ASME V&V 10",
        source=SOURCES["sandia-sand2016-5342c"],
    ),
    "code-verification": Definition(
        term="code verification",
        text=(
            "Establishes that the code accurately solves the mathematical model "
            "incorporated in the code, i.e. that the code is free of mistakes for "
            "the simulations of interest."
        ),
        attributed_to="ASME V&V 20",
        source=SOURCES["sandia-sand2016-5342c"],
    ),
    "solution-verification": Definition(
        term="solution verification",
        text="Estimates the numerical accuracy of a particular calculation.",
        attributed_to="ASME V&V 20",
        source=SOURCES["sandia-sand2016-5342c"],
    ),
    "validation": Definition(
        term="validation",
        text=(
            "The process of determining the degree to which a model is an accurate "
            "representation of the real world from the perspective of the intended "
            "uses of the model."
        ),
        attributed_to="ASME V&V 10 / V&V 20",
        source=SOURCES["sandia-sand2016-5342c"],
    ),
}


class EvidenceKind(StrEnum):
    """Which of ASME's questions a piece of evidence answers."""

    CODE_VERIFICATION = "code-verification"
    SOLUTION_VERIFICATION = "solution-verification"
    VALIDATION = "validation"


#: Every kind of evidence this codebase holds, filed under the question it
#: answers. **The empty tuple is the finding, not a placeholder**: nothing in
#: this repository compares a result against a measurement of a physical part.
KRYOVA_EVIDENCE: Final[dict[EvidenceKind, tuple[str, ...]]] = {
    EvidenceKind.CODE_VERIFICATION: (
        "closed-form identities pinned by the test suite, listed per analysis in "
        "the verification register",
        "published NAFEMS reference solutions, reproduced in app.verify.nafems",
    ),
    EvidenceKind.SOLUTION_VERIFICATION: (
        "a Grid Convergence Index study (app.verify.convergence), on a run that "
        "asks for three or more grids",
    ),
    EvidenceKind.VALIDATION: (),
}

#: The sentence that goes beside every number the product shows. One string in
#: one place: a safety statement with two wordings has two standards.
NOT_VALIDATED: Final = (
    "Not validated. Validation asks whether a model represents the real part, and "
    "only a comparison against measurements of a physical one answers it; no "
    "Kryova result has been compared against a measurement. What exists is "
    "verification: the solver is checked against closed-form and published "
    "benchmark solutions, which shows the equations are being solved correctly, "
    "not that the part will behave as computed."
)

if KRYOVA_EVIDENCE[EvidenceKind.VALIDATION]:  # pragma: no cover - fails at import, on purpose
    raise ValueError(
        "Validation evidence is now recorded, and NOT_VALIDATED still tells every "
        "reader there is none. Rewrite the statement to say what was measured, "
        "against what, before this module may be imported."
    )


def validation_block() -> dict[str, object]:
    """What a published payload says about validation, with the terms quoted."""
    return {
        "validated": bool(KRYOVA_EVIDENCE[EvidenceKind.VALIDATION]),
        "statement": NOT_VALIDATED,
        "evidence": {
            str(kind): list(items) for kind, items in KRYOVA_EVIDENCE.items()
        },
        "terms": [definition.to_dict() for definition in DEFINITIONS.values()],
    }


__all__ = [
    "DEFINITIONS",
    "KRYOVA_EVIDENCE",
    "NOT_OURS",
    "NOT_VALIDATED",
    "NO_STANDARD",
    "SOLID_MECHANICS_SOURCE",
    "SOURCES",
    "STANDARDS",
    "TERMINOLOGY_SOURCE",
    "Definition",
    "EvidenceKind",
    "validation_block",
]
