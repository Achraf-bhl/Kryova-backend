"""Everything that can go wrong inside the geometry kernel, named.

Separate from `app.design.errors` for the reason that file gives about spec errors:
**a kernel error must be distinguishable from a spec error.** The recoveries are
different and the agent picks between them. A `SpecError` means the design is wrong
and CATIA/OCCT was never touched. A `KernelError` means the design was plausible and
the geometry engine could not carry it out — a fillet radius larger than the face it
runs along, a boolean on shapes that do not intersect, an operation the backend has
not implemented yet.

Messages follow the house register set by the route layer: say what is wrong *and what
to do about it*. "Fillet failed" is a shrug; "the 12 mm radius exceeds the 8 mm face it
runs along — reduce it below 8 mm or fillet a different edge" is a fix.
"""

from __future__ import annotations


class KernelError(Exception):
    """The geometry engine could not do what the design asked."""


class KernelUnavailable(KernelError):
    """The kernel backend is not installed or not importable.

    Its own class because the recovery is an install, not a design change, and
    because everything in `app/kernel/` imports on machines that will never have
    OCCT — the same contract `app/catia/` keeps for `pywin32`.
    """


class OperationNotSupported(KernelError):
    """This backend does not implement that operation or capability yet.

    Carries the subject so the conformance harness can report coverage rather than
    just failing: an unimplemented operation is a *known* gap, and the difference
    between "not built yet" and "broken" is what makes a coverage number mean
    anything.

    `subject` is usually an operation name, but it is also used for a capability
    within one — an undecidable edge selector, a primitive kind — so the message is
    phrased to read correctly for both. `tool` remains available under its original
    name because callers and tests reach for it.
    """

    def __init__(self, subject: str, reason: str = "", backend: str = "occt") -> None:
        self.subject = subject
        self.reason = reason
        self.backend = backend
        # `reason` is a separate argument rather than baked into `subject` so the
        # sentence composes: folding an explanation into the subject strands the "yet"
        # in the middle of it.
        #
        # Sentence-cased **here** rather than by the caller, and never with
        # `str.capitalize()`. That method uppercases the first character and lowercases
        # every other one, which turns `BRepFeat_MakePrism` into `brepfeat_makeprism` and
        # `Phase 2.5` into `phase 2.5` — destroying exactly the proper nouns someone
        # reading the error would search for. Doing it in one place means no call site
        # can reach for the wrong method again.
        explanation = f" {_as_sentence(reason)}" if reason else ""
        super().__init__(
            f"The {backend} backend does not support {subject} yet.{explanation} Run "
            "this design on the CATIA backend, or add it to the operation map."
        )

    @property
    def tool(self) -> str:
        """The subject, under the name the operation-facing call sites use."""
        return self.subject


def _as_sentence(text: str) -> str:
    """Capitalise the first letter and end with a full stop, leaving the rest alone."""
    stripped = text.strip().rstrip(".")
    if not stripped:
        return ""
    return f"{stripped[:1].upper()}{stripped[1:]}."


class GeometryError(KernelError):
    """A modelling operation ran and produced nothing usable."""


class NamingError(KernelError):
    """A named element could not be recorded, or could not be found again.

    The failure Layer B exists to prevent, so it is never silent: a name that stops
    resolving after a regeneration means a downstream feature is about to be applied
    to the wrong geometry, and refusing loudly is the only safe answer.
    """


class MeasurementError(KernelError):
    """A quantity was asked for that cannot be measured on this shape."""


__all__ = [
    "GeometryError",
    "KernelError",
    "KernelUnavailable",
    "MeasurementError",
    "NamingError",
    "OperationNotSupported",
]
