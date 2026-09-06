"""The fatigue package's error hierarchy.

Deliberately small. Almost nothing in this package raises: an assessment that
cannot be made comes back as an `UNMEASURED` result naming what was missing,
because that is a *finding an engineer can act on* and an exception is not.
Exceptions are reserved for the two cases that are programming errors rather
than engineering ones — a value that cannot mean anything (a negative cycle
count, a factor of zero) and a backend that was asked to run without being
installed, which `require()` turns into a message naming the pip install.
"""

from __future__ import annotations


class FatigueError(Exception):
    """Base for everything this package raises."""


class BackendUnavailable(FatigueError):
    """The fatigue library is not installed.

    Mirrors `app.kernel.KernelUnavailable`: the package imports on a machine
    without pyLife so the rest of the suite still runs, and refuses at call
    time with an actionable message. Callers that would rather have a result
    than an exception should go through `Assessment.run`, which turns this into
    an `UNMEASURED` verdict naming the missing dependency.
    """


__all__ = ["BackendUnavailable", "FatigueError"]
