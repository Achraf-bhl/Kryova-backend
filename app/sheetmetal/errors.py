"""Everything this package refuses to do, named.

A hierarchy of its own rather than `SpecError` or `RuleError`, for the reason
`app/rules/errors.py` gives about itself: the recovery differs. A
`SheetMetalError` means **the folded part as declared cannot exist or cannot be
made** — the legs overlap, the bend is tighter than the material survives, the
flat pattern folds back over itself. Nothing about the design IR or the
tolerance chain is implicated, and a correction loop that caught one of these as
a spec problem would go and change a compiled plan in response to a bend the
press cannot make.

House register (`app/design/errors.py`, `app/kernel/errors.py`,
`app/manufacture/errors.py`): every message says what is wrong *and what to do
next*. A sheet-metal package is one step before somebody puts a blank in a press
brake, so "unfold failed" is not an acceptable sentence.
"""

from __future__ import annotations


class SheetMetalError(Exception):
    """Base for everything this package refuses."""


class BendError(SheetMetalError):
    """A bend, or its K-factor, is not one that can exist.

    An angle of zero or 180 degrees where a mould line is needed, a negative
    radius, a K-factor outside the physically possible range, a K asked of a
    table that does not cover the material.
    """


class UnfoldError(SheetMetalError):
    """A part cannot be flattened, or was flattened into something uncuttable.

    Raised for geometric impossibility only — legs that consume more length
    than the flange has, a hole off the face it is declared on, two faces
    landing on the same patch of blank. Whether the part can be *formed* is a
    different question and a different answer: see `formability.py`.
    """


class FormabilityError(SheetMetalError):
    """The part is geometrically fine and cannot be manufactured.

    Raised only by `FormabilityReport.raise_for_findings()`, never by a check
    itself, because a shop wants every reason at once rather than the first
    one — see the note in `formability.py` about why the checks return a report
    and the raise is a separate, explicit step.
    """


__all__ = [
    "BendError",
    "FormabilityError",
    "SheetMetalError",
    "UnfoldError",
]
