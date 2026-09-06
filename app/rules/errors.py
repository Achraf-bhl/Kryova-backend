"""Everything this package refuses to construct, named.

A hierarchy of its own rather than `SpecError`, for the reason
`app/requirements/errors.py` gives about itself: the recovery differs. A
`SpecError` means *edit the design and recompile*. A `RuleError` means **the
thing the design is being judged against is wrong** — the limit, its source, the
tolerance chain somebody declared, the feature control frame somebody wrote.
Nothing about the geometry is implicated, and a correction loop that caught one
of these as a spec problem would go off and change a part in response to a typo
in a rule book.

It is not a subclass of `SpecError` for the same reason: `app.design.correct`
and `app.design.compile` both catch `SpecError` broadly, so inheriting would
make a malformed rule vanish into a repair attempt instead of reaching the
person who wrote it.

House register: every message says what is wrong *and what to do next*.
"""

from __future__ import annotations


class RuleError(Exception):
    """A design rule, a tolerance stack or a feature control frame is wrong."""


class VocabularyError(RuleError):
    """A rule names a quantity nothing in this system can measure.

    Raised at construction, never at evaluation. A rule whose measurement can
    never arrive is not a rule that fails — it is a rule that is `UNMEASURED`
    for ever, and a DFM suite full of those reports green on a part nobody
    checked. The refusal names the measurable paths and says where a new one
    would have to be declared (`app/kernel/contract.py`).
    """


class SourceError(RuleError):
    """A limit arrived with no defensible source, or claims an adoption it has not had.

    Decision 3 applied to a rule rather than to a measurement: a number nobody
    can trace is a number nobody can argue with, and a design rule is exactly
    the place where an undefended number becomes a red build.
    """


class StackUpError(RuleError):
    """A tolerance chain is malformed, or a statistical result was asked for
    from data that cannot support one."""


class GdtError(RuleError):
    """A feature control frame, datum or datum reference frame is internally
    inconsistent."""


__all__ = [
    "GdtError",
    "RuleError",
    "SourceError",
    "StackUpError",
    "VocabularyError",
]
