"""Everything that can be wrong with a requirement, named.

A separate hierarchy from `app.design.errors.SpecError`, deliberately, and the
reason is the same one that put `SpecError` beside `CatiaOperationError`: the
recovery differs. A `SpecError` means *edit the design and recompile*. A
`RequirementError` means *the specification the design is being judged against
is wrong* — nothing about the geometry is implicated, and a correction loop that
caught it as a spec problem would go off and change a part in response to a typo
in a requirements document.

It is also not a subclass, because `app.design.correct` and `app.design.compile`
both catch `SpecError` broadly. Inheriting would make a malformed requirement
disappear into a repair attempt instead of being reported to the person who
wrote it.

The messages follow the house register: say what is wrong *and what to do about
it*.
"""

from __future__ import annotations


class RequirementError(Exception):
    """The requirement specification is wrong. No geometry is implicated."""


class VocabularyError(RequirementError):
    """A requirement constrains a quantity nothing in this system can measure.

    Raised at construction, not at verification. A requirement whose measurement
    path is a typo would otherwise verify as `UNMEASURED` — honest, and
    indistinguishable from a quantity nobody got round to measuring, which is
    exactly the confusion `app.kernel.contract` exists to prevent one layer down.
    """


class RequirementCycleError(RequirementError):
    """The decomposition graph has a cycle. The message names the loop.

    "Why is this rib here" is answered by walking parents upward. A cycle makes
    that walk non-terminating and makes the question unanswerable, so it is
    refused when the set is built rather than discovered by whoever asks.
    """


class TraceError(RequirementError):
    """A trace link points at a requirement, feature or evidence that is not there."""


class RequirementParseError(RequirementError):
    """A requirements document could not be read, and the message says which lines.

    Never raised for *part* of a document. A parse that returns the lines it
    understood and silently drops the rest is the failure mode the importer
    exists to refuse, so either every line is accounted for or the import is
    refused with all of the problems listed at once.
    """


__all__ = [
    "RequirementCycleError",
    "RequirementError",
    "RequirementParseError",
    "TraceError",
    "VocabularyError",
]
