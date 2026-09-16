"""Project Chrono across a container boundary (master plan E9.1).

Reading order: `run.py` (the process boundary, and why there is one — it is a
**packaging** boundary, not a licence one), `payload.py` (the JSON that crosses it),
`_entrypoint.py` (what executes inside the container — it imports nothing from `app`),
`engine.py` (the `DynamicsEngine` the rest of the product sees).

**What this package can claim.** The translation is written against Chrono's documented
API with the symbol names resolved at run time rather than spelled once, because Chrono 9
renamed a great deal of its surface and both spellings are in circulation. What has
**not** happened is a verified solve: no answer has been checked against a closed-form
result, so every result carries `engine.UNVERIFIED_NOTE` and
`docs/WINDOWS_VERIFICATION.md` carries the oracle runs that would settle it. Until they
are done, `app.dynamics.engine.engines()` will not fall through to this engine — it has
to be asked for by name.

**Nothing here is imported by `app.dynamics.engine`.** That module's `engines()` imports
`ContainerChronoEngine` inside the function, so importing the dynamics seam does not
import a subprocess launcher, and `app.dynamics.engine.ChronoEngine` — the in-process
route, still shut, still refusing — stays independent of this package entirely.
"""

from app.dynamics.chrono.engine import (
    POINT_MASS_NOTE,
    UNVERIFIED_NOTE,
    ContainerChronoEngine,
)
from app.dynamics.chrono.payload import WIRE_VERSION
from app.dynamics.chrono.run import (
    DEFAULT_IMAGE,
    DEFAULT_TIMEOUT_S,
    ChronoUnavailable,
    availability,
    engine_identity,
)

__all__ = [
    "DEFAULT_IMAGE",
    "DEFAULT_TIMEOUT_S",
    "POINT_MASS_NOTE",
    "UNVERIFIED_NOTE",
    "WIRE_VERSION",
    "ChronoUnavailable",
    "ContainerChronoEngine",
    "availability",
    "engine_identity",
]
