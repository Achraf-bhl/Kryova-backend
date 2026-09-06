"""What a rule is allowed to name, and which way a sampled answer is wrong.

Two jobs, both of them about not being lied to.

**1. A rule may only name a documented quantity.** `app/kernel/contract.py` is
the written vocabulary of numbers a design may assert on, and its
`undocumented_paths()` being asserted empty by the kernel tests is what makes it
a contract rather than a comment. This module is the other half of that deal:
a rule naming `hole_to_edge_mm` is refused **at construction**, because nothing
measures it, so the rule would report `UNMEASURED` for ever — a DFM gate that
never fires reads exactly like one that always passes.

**2. A sampled answer is a bound, and the direction of the bound decides
whether a pass means anything.** `minimum_wall_mm` is found by casting rays from
a finite set of points, so the reported value is an **upper bound** on the true
minimum: a thin spot between two samples is missed. Read that against a rule and
the asymmetry is stark, and it is the whole reason this table exists:

* `minimum_wall_mm >= 2.5` measuring **2.0** — the true minimum is at most 2.0,
  so it is certainly below 2.5. The violation is **proved**.
* `minimum_wall_mm >= 2.5` measuring **2.6** — the true minimum is *at most*
  2.6 and could be anything below it. The pass is **not proved**. Nothing about
  a denser scan makes this a proof either; it only moves the bound.

So a rule result carries `provisional`, and `RuleReport.proven` is a different
question from `RuleReport.ok`. The alternative — reporting such a pass as
`UNMEASURED` — was considered and rejected: every ray-cast wall check would be
unmeasured for ever, and a suite that is red for ever is a suite somebody
switches off (the lesson `app/design/missions.py` records about its eight
pending rungs). A provisional pass is a screening result, and it says so.

**The direction cannot be read off the provenance sidecar**, which records
`APPROXIMATED` and the method but not which way the error goes. It is a property
of what the scan does, so it is declared here, once, beside the reasoning — and
`tests/test_rules_engine.py` asserts that every quantity the contract marks
`APPROXIMATED` has a direction declared, so a new sampled quantity cannot be
added to the kernel and silently acquire a sound-looking pass.

**The contract is imported lazily**, exactly as `app.design.assertions` imports
`app.kernel.provenance` lazily and for the same measured reason: importing
`app.kernel.contract` executes `app/kernel/__init__.py`, which pulls in the OCCT
binding and ~166 MB of `OCP` (1.2 s here). A rule set that is only being written
out, rendered or serialised never pays that; only constructing or checking a
rule does.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final

from app.rules.errors import VocabularyError


class BoundDirection(StrEnum):
    """Which side of the truth a sampled number falls on.

    `EXACT` is the answer for anything integrated or evaluated in closed form,
    and is what a `MEASURED` provenance basis means. The other two are only
    consulted when the payload's own sidecar says the number was approximated,
    so a backend that measures wall thickness exactly is never penalised for the
    fact that another one samples it.
    """

    #: The reported value is greater than or equal to the truth. Every "minimum
    #: found by sampling" is one of these.
    UPPER_BOUND = "upper_bound"

    #: The reported value is less than or equal to the truth. Every "count of
    #: things found by sampling" is one of these — the scan can miss one, it
    #: cannot invent one.
    LOWER_BOUND = "lower_bound"

    #: Approximated with no known direction — a derivation, an interpolation. No
    #: pass on such a number is provable, and it is treated as the conservative
    #: case rather than being assumed benign.
    UNKNOWN = "unknown"

    #: Not approximated at all.
    EXACT = "exact"


#: Which way each sampled quantity errs, with the reason it errs that way.
#: Keyed on the contract's own path spelling so a rename in the contract shows up
#: here as a missing key rather than as a silently wrong direction.
SAMPLED_BOUNDS: Final[Mapping[str, BoundDirection]] = {
    # Rays are cast from a finite grid of points, so the thinnest wall *found*
    # is at least as thick as the thinnest wall there is.
    "minimum_wall_mm": BoundDirection.UPPER_BOUND,
    # Same argument, in the same units: the tightest concave radius sampled is
    # no smaller than the tightest one present.
    "minimum_concave_radius_mm": BoundDirection.UPPER_BOUND,
    # A visibility scan can fail to notice an undercut face; it cannot report a
    # face that is reachable as one that is not. So the count under-reports.
    "undercut_face_count": BoundDirection.LOWER_BOUND,
    # Where the thinnest sample was found. A location, not a magnitude — no
    # inequality against it means anything, and a rule that compares one
    # component of it to a limit is getting a provisional answer whichever way
    # it points.
    "thinnest_point_mm": BoundDirection.UNKNOWN,
    # Derived from the designation's pitch through the ISO profile rather than
    # measured off the cylinder. Close, and not a bound in either direction.
    "thread.minor_diameter_mm": BoundDirection.UNKNOWN,
}


def _contract() -> Any:
    """`app.kernel.contract`, imported on use. See this module's docstring."""
    from app.kernel import contract

    return contract


def is_measurable(path: str) -> bool:
    """Whether the measurement contract documents this path."""
    entry = _contract().entry(path)
    return entry is not None and not entry.superseded_by


def require_measurable(path: str, *, rule_name: str) -> None:
    """Refuse a path nothing measures, saying what to do about it.

    Three refusals, in descending order of how much can be said:

    1. The path is **superseded** — the contract kept the old spelling on
       purpose, so the message names its replacement instead of pretending the
       quantity vanished.
    2. The path is **undocumented** — a typo, or a spelling borrowed from
       another CAD system. The message lists a sample of what *is* measurable
       and names the file a genuinely new quantity has to be declared in.
    3. It is fine.
    """
    contract = _contract()
    entry = contract.entry(path)
    if entry is not None and entry.superseded_by:
        raise VocabularyError(
            f"{rule_name}: {path!r} was superseded by {entry.superseded_by!r}. "
            f"Write the rule against {entry.superseded_by!r}."
        )
    if entry is None:
        sample = ", ".join(sorted(item.path for item in contract.QUANTITIES)[:8])
        raise VocabularyError(
            f"{rule_name}: nothing in this system measures {path!r}, so a rule on it "
            f"could never be checked — it would report UNMEASURED for ever, which reads "
            f"exactly like a gate that always passes. Measurable quantities include: "
            f"{sample}, … (the full list is app.kernel.contract.catalogue()). If this "
            f"quantity ought to exist, add the scan that produces it and declare it in "
            f"app/kernel/contract.py first."
        )


def describe(path: str) -> str:
    """The contract's one-line description of a path, for a message or a report."""
    return str(_contract().describe(path))


def unit(path: str) -> str:
    """The unit the contract declares for a path, or an empty string if undocumented."""
    entry = _contract().entry(path)
    return "" if entry is None else str(entry.unit)


def typical_basis_is_approximated(path: str) -> bool:
    """Whether the contract says this quantity is *normally* sampled.

    Documentation, not enforcement — the payload's own sidecar is the truth for
    a given run, which is the rule `app/kernel/contract.py` states about itself.
    Used only to decide whether a direction must be declared for a path.
    """
    from app.kernel import provenance

    entry = _contract().entry(path)
    return entry is not None and entry.typical_basis is provenance.Basis.APPROXIMATED


def payload_states_a_basis(payload: Mapping[str, Any], path: str) -> bool:
    """Whether a measurement payload's sidecar says anything about this path at all.

    Deliberately three-valued collapsed to two: `app.kernel.provenance.basis_of`
    returns `None` for a path the payload makes no claim about, and its own
    docstring is emphatic that `None` is not `MEASURED`. Callers here need to
    tell "the backend said how it got this" from "the backend said nothing",
    because the second is the case where the contract's `typical_basis` is the
    only thing left to read.
    """
    from app.kernel import provenance

    return provenance.basis_of(payload, path) is not None


def bound_direction(path: str) -> BoundDirection:
    """Which way a sampled value of this quantity errs.

    `EXACT` for anything not normally sampled. For a sampled quantity with no
    declared direction the answer is `UNKNOWN`, which makes every pass on it
    provisional — the conservative reading, and the one that cannot manufacture
    confidence for a quantity nobody has thought about yet.
    """
    normalised = str(_contract().normalise(path))
    declared = SAMPLED_BOUNDS.get(normalised)
    if declared is not None:
        return declared
    if typical_basis_is_approximated(normalised):
        return BoundDirection.UNKNOWN
    return BoundDirection.EXACT


__all__ = [
    "SAMPLED_BOUNDS",
    "BoundDirection",
    "bound_direction",
    "describe",
    "is_measurable",
    "payload_states_a_basis",
    "require_measurable",
    "typical_basis_is_approximated",
    "unit",
]
