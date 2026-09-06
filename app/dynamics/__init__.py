"""Multibody dynamics -- master plan Phase 9, *where load cases actually come from*.

Today a `LoadCase` is something a person types: 5 kN on that face. On a real machine the
load on a bracket is whatever the mechanism *does* to it -- peak inertia at the end of a
stroke, the reaction at a bearing through a cycle. This package is the step that produces
the second kind, and `loadcases.py` lands it in the *existing* `app.solve` vocabulary
rather than beside it, because Decision 2 names that vocabulary as the asset and a
second one would fork the system.

Reading order:

1. `types.py` -- the vocabulary: bodies, joints, drivers, a motion range; and what comes
   back: motion and joint reactions over time, at named locations. **This is the part
   that is ours and is never delegated to a library.**
2. `kinematics.py` -- where everything is, exactly, with no engine. Closed-form
   serial-chain recursion: no time stepping, so no integration error.
3. `closures.py` -- the two planar closed loops a forward recursion cannot reach,
   slider-crank and four-bar, solved in closed form with analytic derivatives.
4. `reactions.py` -- Newton-Euler inverse dynamics: what every joint carries. Master
   plan 9.4, the step that produces a defensible load.
5. `loadcases.py` -- reaction plus inertia to an `app.solve.types.LoadCase`. The loop
   that closes the system.
6. `clearance.py` -- does it clash anywhere in its travel. Master plan 9.3 and gate G3.
7. `engine.py` -- the seam an engine drops into, and an honest account of which ones are
   behind it.

**What is exercised and what is a seam, stated plainly because the difference matters.**

*Exercised against closed-form answers* (`tests/test_dynamics_*.py`): the kinematic
recursion (free fall, a rotating body's centripetal acceleration, a driven pendulum, a
telescoping arm's Coriolis term), the closures (slider-crank position and acceleration,
four-bar loop closure), the inverse-dynamics reactions (a hanging mass, a whirling mass,
a two-link chain checked for free-body consistency), and the load-case bridge.

*A seam, and not yet a capability*: `engine.ChronoEngine`. Its availability probe is real
and tested; its `simulate` refuses. PyChrono could not be installed on this deployment --
see that class's docstring for the measured finding, which is the same finding
Decision 1 already recorded for `pythonocc-core`, and which is why `pychrono` is
deliberately absent from `requirements.txt`.

*Not attempted at all*, and therefore not represented by any code that could be mistaken
for it: contact, friction, springs, end stops, flexible bodies, and any case where the
motion is an output rather than an input. Every entry point that could be asked for one
refuses by name.
"""

from app.dynamics.errors import DynamicsError, EngineUnavailable, MechanismError
from app.dynamics.types import (
    GRAVITY_DOWN_MM_S2,
    Body,
    BodyMotion,
    Driver,
    Joint,
    JointReaction,
    Mechanism,
    MechanismResult,
    MotionPath,
    MotionRange,
)

__all__ = [
    "GRAVITY_DOWN_MM_S2",
    "Body",
    "BodyMotion",
    "Driver",
    "DynamicsError",
    "EngineUnavailable",
    "Joint",
    "JointReaction",
    "Mechanism",
    "MechanismError",
    "MechanismResult",
    "MotionPath",
    "MotionRange",
]
