"""What can go wrong in a mechanism, said in a way that names the next move.

Three failures, and they are genuinely different recoveries — which is the same
argument `app.design.assertions` makes for keeping `FAILED` and `UNMEASURED` apart:

* `MechanismError` — the mechanism as *described* is not a mechanism. A joint names a
  body that does not exist, two bodies claim the same name, a link is too short to
  close the loop. Fix the definition.
* `EngineUnavailable` — the mechanism is fine and nothing can run it. Install
  something, or ask for less.
* `DynamicsError` — the base, so a caller that does not care can catch one thing.

Every message here is written to end with what to do, per the repository's error
convention. "Invalid mechanism" is not an acceptable string in this package.
"""

from __future__ import annotations


class DynamicsError(RuntimeError):
    """Something in the multibody layer could not be done as asked."""


class MechanismError(DynamicsError):
    """The mechanism is not well formed, or asks for something this layer cannot pose."""


class EngineUnavailable(DynamicsError):
    """No dynamics engine is installed that can run this.

    Carried rather than swallowed: a mechanism that could not be simulated must not
    silently become a mechanism with no loads. Callers that want the softer form ask
    the engine for `availability()` first and get a reason without an exception.
    """


__all__ = ["DynamicsError", "EngineUnavailable", "MechanismError"]
