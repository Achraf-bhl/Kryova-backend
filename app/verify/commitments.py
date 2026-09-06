"""What Kryova will not claim — P10.3's second page, as data.

A "what we will not claim" page written as prose is a page a lawyer wrote, and
its readers know it. The only version worth publishing is one where every
commitment names **the place in the software that enforces it**, so a sceptical
engineer can stop reading and go and check.

That is the whole design decision here. A `Commitment` carries a `we_will_not`
in plain words and an `enforced_by` list of files that exist in this repository.
`tests/test_trust.py` asserts that every one of those paths is on disk, so the
page cannot rot into a set of claims about code that was renamed or deleted. A
commitment whose enforcement is a policy rather than a mechanism says so, in
`enforcement`, rather than being dressed up with a file that does not really
check it — Decision 5's scope limit is exactly such a commitment, and pretending
a module enforces it would be the first thing on this page to be untrue.

The two kinds are kept apart deliberately:

* `Enforcement.MECHANICAL` — the software refuses. `Target.__post_init__` will
  not construct a published target with no citation; `ConvergenceStudy` returns
  `None` for a value it will not permit out. These can be broken by a test and
  the test goes red.
* `Enforcement.POLICY` — a human decision this codebase is written to, not a
  runtime check. "No unattended sign-off on a safety-critical machine" is a
  statement about what the product is *for*; no assertion can enforce it. Saying
  so is the honest form, and the field exists so a reader can tell the two
  apart at a glance instead of assuming all of it is verified.

Decision 5's scope statement is reproduced verbatim from the master plan and is
the first entry, because it is the one everything else is a consequence of.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class Enforcement(StrEnum):
    """How a commitment is kept."""

    #: The code refuses. Break it and a named test fails.
    MECHANICAL = "mechanical"
    #: A decision about scope and product design. No runtime check can hold it,
    #: and claiming one could would itself be a false claim.
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class Commitment:
    """One thing Kryova will not do, and where a reader can check it."""

    id: str
    #: The commitment in the negative, because the negative is the part that
    #: costs something. "We are honest about coverage" is a slogan; "we will not
    #: report an unmeasured claim as a pass" is a commitment.
    we_will_not: str
    why: str
    enforcement: Enforcement
    #: Repository-relative paths a reader can open. Asserted to exist.
    enforced_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.we_will_not.strip() or not self.why.strip():
            raise ValueError(f"Commitment {self.id!r} must say what and why.")
        if self.enforcement is Enforcement.MECHANICAL and not self.enforced_by:
            raise ValueError(
                f"Commitment {self.id!r} claims mechanical enforcement and names no "
                "file. A mechanism nobody can point at is a policy with better "
                "marketing; declare it POLICY or name the code."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "we_will_not": self.we_will_not,
            "why": self.why,
            "enforcement": str(self.enforcement),
            "enforced_by": list(self.enforced_by),
        }


#: Decision 5, verbatim from KRYOVA_MASTER_PLAN.md. Quoted rather than
#: paraphrased: a paraphrase of a scope limit is how a scope limit widens.
SCOPE: Final = (
    "Kryova designs, analyses and documents the structural, kinematic and packaging "
    "content of a machine, integrating bought-in functional components, to a standard "
    "a licensed engineer can review and sign."
)

#: The sign-off model, stated as the thing a customer is buying rather than as a
#: disclaimer. Decision 5's own words for it.
SIGN_OFF = (
    "A licensed engineer signs. Kryova does the engineering content and produces the "
    "evidence a reviewer needs — the geometry, the analysis, the provenance chain from "
    "requirement to number — and a qualified human being reviews it and accepts "
    "responsibility for it. Unattended sign-off on a safety-critical machine is not a "
    "feature that is coming later; it is outside what this product is for."
)


COMMITMENTS: Final[tuple[Commitment, ...]] = (
    Commitment(
        id="no-unattended-sign-off",
        we_will_not=(
            "We will not offer, and will not build towards, unattended sign-off on a "
            "safety-critical machine. No output of this system is an approval."
        ),
        why=(
            "Signing accepts liability, and liability belongs to a person with a "
            "licence and a professional body behind it. A product that blurred that "
            "would be selling the one thing it cannot deliver."
        ),
        enforcement=Enforcement.POLICY,
    ),
    Commitment(
        id="bought-in-components-are-integrated",
        we_will_not=(
            "We will not claim to design the bought-in functional components of a "
            "machine — engines, bearings, drives, brakes. Kryova integrates them."
        ),
        why=(
            "It is what the industry actually does, and claiming otherwise would put "
            "a customer's trust on the part of the work that is least ours."
        ),
        enforcement=Enforcement.POLICY,
    ),
    Commitment(
        id="unmeasured-is-never-a-pass",
        we_will_not=(
            "We will not report a claim nobody could measure as a pass. An assertion "
            "that could not be evaluated comes back UNMEASURED, and an UNMEASURED "
            "result never counts as green."
        ),
        why=(
            "A suite that quietly skips what it could not read reports success on a "
            "part nobody checked. That is the single failure mode that would make "
            "every other number here worthless."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=(
            "app/design/assertions.py",
            "app/observe/report.py",
            "app/verify/benchmarks.py",
            "tests/test_design_assertions.py",
        ),
    ),
    Commitment(
        id="unconverged-numbers-are-not-stated",
        we_will_not=(
            "We will not state a simulation result whose mesh convergence study did "
            "not converge. The number is withheld, not flagged."
        ),
        why=(
            "An unconverged number is worse than no number: it is the mesh's answer "
            "wearing the model's clothes, and a reader cannot tell it apart from a "
            "converged one."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/verify/convergence.py", "tests/test_verify_convergence.py"),
    ),
    Commitment(
        id="a-benchmark-target-must-be-citable",
        we_will_not=(
            "We will not publish a validation target we cannot cite. A target whose "
            "published value we could not verify is recorded as UNKNOWN and is "
            "forbidden from carrying a number at all."
        ),
        why=(
            "A recalled or reverse-engineered reference value converts 'we have not "
            "validated this' into 'we validated this and it passed', and nobody "
            "downstream can tell the difference."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/verify/benchmarks.py", "tests/test_verify_benchmarks.py"),
    ),
    Commitment(
        id="what-is-not-validated-is-published",
        we_will_not=(
            "We will not publish a validation register that lists only what passed. "
            "Every analysis the product reports a number from appears, validated or "
            "not, with the reason."
        ),
        why=(
            "A register of three green rows on a product with eleven analyses is a "
            "marketing page. The denominator is the information."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/verify/register.py", "tests/test_verify_register.py"),
    ),
    Commitment(
        id="a-result-is-bound-to-what-produced-it",
        we_will_not=(
            "We will not report a result detached from the geometry, mesh, material, "
            "load case and solver version that produced it, and we will not invent a "
            "solver version that a solver does not declare."
        ),
        why=(
            "Without that binding a stress figure cannot be reproduced or re-checked, "
            "and no engineer can sign it."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/verify/provenance.py", "tests/test_verify_provenance.py"),
    ),
    Commitment(
        id="approximated-is-not-measured",
        we_will_not=(
            "We will not present a sampled or approximated measurement as a measured "
            "one. A wall thickness from a finite ray set is an upper bound and says so; "
            "a clearance checked at N poses says a collision between two poses is "
            "invisible to it."
        ),
        why=(
            "A sampled bound that reads as exact is how a part passes a check it never "
            "actually passed."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=(
            "app/kernel/provenance.py",
            "app/kernel/interrogation.py",
            "app/dynamics/clearance.py",
            "tests/test_dynamics_clearance.py",
        ),
    ),
    Commitment(
        id="the-visual-check-is-not-a-sign-off",
        we_will_not=(
            "We will not treat a vision model's opinion of a rendered part as approval. "
            "The visual review can object; it has no way to approve."
        ),
        why=(
            "It is a filter that catches a part built obviously wrong. Every way it can "
            "fail to run is 'unchecked', which is not a pass."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/ai/vision.py", "tests/test_vision.py"),
    ),
    Commitment(
        id="a-missing-translation-is-reported-missing",
        we_will_not=(
            "We will not substitute an English CAD term for a localised one we do not "
            "have. A missing translation is reported as missing."
        ),
        why=(
            "An engineer can work with 'I do not have the German name, it is here in "
            "the menu'. Nobody recovers from being sent to a menu item that does not "
            "exist."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/catia_kb/", "tests/test_catia_kb.py"),
    ),
    Commitment(
        id="attachments-are-data-not-instructions",
        we_will_not=(
            "We will not let text extracted from a file you upload act as an "
            "instruction. It is quoted material, it never enters a system prompt, and "
            "no tool action is justified by it without you seeing that justification."
        ),
        why=(
            "Document-borne prompt injection is a real attack class against an agent "
            "that reads customer drawings and specifications, not a hypothetical."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/documents/", "tests/test_documents_injection.py"),
    ),
    Commitment(
        id="the-kernel-that-drew-it-is-named",
        we_will_not=(
            "We will not answer a question about a part with a plausible substitute "
            "produced by a different kernel without saying so."
        ),
        why=(
            "A picture of the wrong part is worse than a refusal, because a refusal "
            "names the backend it needs and a picture does not."
        ),
        enforcement=Enforcement.MECHANICAL,
        enforced_by=("app/api/routes/kernel.py", "tests/test_kernel_routes.py"),
    ),
)

BY_ID: Final[dict[str, Commitment]] = {c.id: c for c in COMMITMENTS}

if len(BY_ID) != len(COMMITMENTS):  # pragma: no cover - a duplicate is a typo
    raise ValueError("two commitments share an id")


def to_dict() -> dict[str, Any]:
    """The page, as data."""
    mechanical = [c for c in COMMITMENTS if c.enforcement is Enforcement.MECHANICAL]
    policy = [c for c in COMMITMENTS if c.enforcement is Enforcement.POLICY]
    return {
        "scope": SCOPE,
        "sign_off": SIGN_OFF,
        "summary": {
            "commitments": len(COMMITMENTS),
            "mechanically_enforced": len(mechanical),
            "policy_only": len(policy),
        },
        "commitments": [c.to_dict() for c in COMMITMENTS],
    }


__all__ = [
    "COMMITMENTS",
    "SCOPE",
    "SIGN_OFF",
    "Commitment",
    "Enforcement",
    "to_dict",
]
