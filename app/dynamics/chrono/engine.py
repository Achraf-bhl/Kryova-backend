"""`ContainerChronoEngine` — Chrono behind the `DynamicsEngine` seam (master plan E9.1).

The engine `app.dynamics.engine.ChronoEngine` refuses to be. That class stays, unchanged
and still refusing, and the two are deliberately separate rather than one class with a
branch:

* **`ChronoEngine` is the in-process route and is still shut.** Its refusal is about
  `pip`, and it is still true — `pip install pychrono` still installs an unrelated timing
  utility, and nothing here changes that.
* **`ContainerChronoEngine` is the route that exists**: a container with its own conda,
  a JSON file in and a JSON file out.

Folding them together would mean one `availability()` answering two different questions
with one sentence, and the sentence a user needs is exactly which of the two routes they
have.

**What this engine can and cannot claim, stated before anything else.** The translation in
`_entrypoint.py` is written against Chrono's documented API **with the symbol names
checked against a real PyChrono 9.0.1 build** rather than recalled — the same discipline
`app/solve/calculix/` was written with, and for the same reason: shipping code that calls
a name that is not there wastes a session on an `AttributeError`. What has **not**
happened is a verified solve: no answer this engine produces has been checked against a
closed-form result on this machine, so `simulate` marks every result accordingly and
`docs/WINDOWS_VERIFICATION.md` carries the oracle runs that would settle it.

That is a weaker claim than `KinematicEngine`'s, which is checked against closed form, and
it is why `engines()` still puts the kinematic engine where it can answer: **a prescribed
serial chain must not silently start coming from an unverified integrator** when an exact
evaluator can answer it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.config import settings
from app.dynamics.chrono import payload as wire
from app.dynamics.chrono.run import (
    DEFAULT_IMAGE,
    availability,
    engine_identity,
    run_mechanism,
)
from app.dynamics.engine import DynamicsEngine, EngineAvailability
from app.dynamics.types import Mechanism, MechanismResult, MotionRange

#: Appended to every result until an oracle run on real hardware says otherwise. It is a
#: `warnings` entry rather than prose in a docstring because it has to travel with the
#: numbers -- a caveat only a developer reads is not a caveat on a result.
UNVERIFIED_NOTE = (
    "This result came from Project Chrono through Kryova's container boundary, and "
    "Kryova's translation into a Chrono system has never been checked against a "
    "closed-form answer on this deployment. Treat the numbers as unverified until "
    "docs/WINDOWS_VERIFICATION.md's Chrono oracle runs have been done."
)

#: Every body is a point mass: `app.assembly.mass.roll_up` carries no inertia tensor, so
#: none is sent. Travels with the result for `UNVERIFIED_NOTE`'s reason.
POINT_MASS_NOTE = (
    "Every body was sent as a point mass. Kryova's mass roll-up has no inertia tensor "
    "(master plan E9.2), so a body's resistance to angular acceleration is not modelled "
    "and any moment that depends on it is absent rather than approximate."
)


class ContainerChronoEngine(DynamicsEngine):
    """Project Chrono, run in a container because PyChrono ships only through conda."""

    name = "chrono-container"

    covers = (
        "closed loops, contact, friction, springs and end stops -- what the kinematic "
        "engine refuses -- but through an unverified translation"
    )

    def __init__(
        self,
        *,
        launcher: str | None = None,
        image: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._launcher = launcher or settings.chrono_launcher
        self._image = image or settings.chrono_image or DEFAULT_IMAGE
        self._timeout_s = (
            timeout_s if timeout_s is not None else settings.chrono_timeout_s
        )

    def availability(self) -> EngineAvailability:
        missing = availability(self._launcher, self._image)
        if missing:
            return EngineAvailability(
                engine=self.name,
                available=False,
                covers=self.covers,
                reason=missing,
            )
        # The image's content id, not its tag: a tag can be rebuilt to point at different
        # bytes, and a version a result is bound to has to be the bytes.
        identity = engine_identity(self._launcher, self._image) or self._launcher
        return EngineAvailability(
            engine=self.name,
            available=True,
            version=identity,
            covers=self.covers,
        )

    def simulate(self, mechanism: Mechanism, motion: MotionRange) -> MechanismResult:
        """Run the mechanism in the container and read back the motion and reactions."""
        self.require()
        body = wire.to_payload(mechanism, motion)

        # A fresh directory per run, deleted with it. Two runs sharing one would let the
        # second read the first's `output.json` when it failed to write its own -- a
        # stale answer presented as a fresh one, which is the failure the whole
        # file-boundary design is otherwise careful about.
        with tempfile.TemporaryDirectory(prefix="kryova-chrono-") as work:
            _, written = run_mechanism(
                Path(work),
                body,
                launcher=self._launcher,
                image=self._image,
                timeout_s=self._timeout_s,
            )

        result = wire.from_result(mechanism, written, engine=self.name)
        notes = (*result.warnings, UNVERIFIED_NOTE, POINT_MASS_NOTE)
        # Rebuilt rather than mutated: `MechanismResult` is frozen, and the note has to be
        # on the path's warnings too, because that is what a report prints.
        return MechanismResult(
            mechanism=result.mechanism,
            engine=result.engine,
            path=result.path,
            reactions=result.reactions,
            warnings=notes,
        )


__all__ = ["POINT_MASS_NOTE", "UNVERIFIED_NOTE", "ContainerChronoEngine"]
