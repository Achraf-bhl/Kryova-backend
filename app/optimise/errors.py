"""Everything that can be wrong with an optimisation *statement*, named.

The split this hierarchy exists to keep is the same one `app/design/errors.py`
makes and for the same reason: **a badly posed problem is not a failed run.**
Asking to minimise a measurement no backend reports, or giving a design variable
a lower bound above its upper bound, is wrong before anything is built — the
recovery is to edit the problem. A run that built forty parts and never reached
a feasible one is a *result*, not an exception, and comes back as an
`OptimisationResult` saying so.

So nothing in this package raises to report non-convergence. That would let a
caller wrap the run in `try/except` and treat "it did not converge" as an
infrastructure hiccup to retry, which is exactly the confusion Decision 3 is
written against.
"""

from __future__ import annotations


class OptimisationError(Exception):
    """The optimisation problem is wrong. Nothing was built."""


class VariableError(OptimisationError):
    """A design variable is unbounded, inverted, unknown to the model, or duplicated."""


class ObjectiveError(OptimisationError):
    """The objective names something that cannot be read as a number."""


class DriverUnavailable(OptimisationError):
    """The requested optimiser is not installed here.

    Its own class because the recovery is an install, not an edit — and because
    the message has to name the alternative that *is* present rather than leave
    a caller with a bare ImportError from a library they did not know was
    optional.
    """


__all__ = [
    "DriverUnavailable",
    "ObjectiveError",
    "OptimisationError",
    "VariableError",
]
