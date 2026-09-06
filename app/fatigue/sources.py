"""The rule every fatigue input obeys: say where the number came from.

One function, deliberately. It is the smallest scale at which Decision 3
applies — *an unmeasured claim is never a pass* — and Phase 8's whole honest
boundary rests on it. A high-cycle assessment of a welded joint, a notch or a
casting is only as good as a handful of judgements: which detail category, what
surface finish, which size effect, what scatter to allow. Every one of those is
a number a qualified engineer chooses, and the failure mode this package is
built to avoid is the one where such a number sits as a default inside a
function, gets used a hundred times, and nobody can say afterwards what was
assumed.

So there are no default factors anywhere in `app/fatigue/`. There is no
`surface_factor=0.9`. Every factor is an explicit input, and constructing one
without a source raises rather than warns — a warning is a thing that scrolls
past.

The source string is free text on purpose. It is read by a person reviewing the
assessment, not parsed; "Shigley 10th ed. Fig. 6-26, machined, Sut 690 MPa" and
"supplier test report TR-2291 §4" are both exactly what is wanted, and no
schema would improve either.
"""

from __future__ import annotations


def require_source(source: str, what: str) -> str:
    """Return the trimmed source, or refuse the input for not having one.

    Raises `ValueError` rather than returning a sentinel: this is a
    construction-time programming error, not an engineering finding, and the
    object must not come into existence. Engineering findings — a factor that
    is *absent* from an assessment that needs it — come back as an `UNMEASURED`
    result instead, from `app.fatigue.assessment`.
    """
    text = source.strip()
    if not text:
        raise ValueError(
            f"{what} must name its source — the datasheet, standard, test report or "
            "measurement it came from. A fatigue input with no provenance cannot be "
            "reviewed, and an unreviewable input is what this package exists to prevent."
        )
    return text


__all__ = ["require_source"]
