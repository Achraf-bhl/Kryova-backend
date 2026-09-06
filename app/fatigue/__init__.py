"""Fatigue and durability — master plan Phase 8.

*Structures fail from fatigue, not from a single static load.* `app/solve/` answers
"does it survive this instant"; nothing before this package answered "does it survive
ten million of them". This is that answer, and the shape of it is set by two of the
project's standing decisions pulling in the same direction.

**Decision 2 — physics is federated.** The two textbook steps, rainflow counting and
Palmgren–Miner summation, are done by **pyLife** (Bosch Research, Apache-2.0), not
here. `backend.py` is the seam; nothing above it does fatigue arithmetic.

**Decision 3 — verification is the product.** The 20% of this phase that is library is
federated; the 80% that is methodology is what these modules are. And methodology, for
fatigue, means being explicit about a handful of numbers that decide the answer and
that a *qualified engineer chooses* — the S-N curve, the weld detail category, the
surface and size factors, the notch sensitivity, the mean-stress model, the duty
cycle. None of them has a default anywhere in this package. Every one is an input
carrying the source it was read from, and an assessment missing one comes back
`UNMEASURED` naming it rather than returning a life.

Reading order:

```
app/fatigue/
  sources.py     the one rule every input obeys: say where the number came from
  history.py     a load *history*, and the two attributes that decide whether it
                 can be assessed at all — where the stress was read, and whether
                 it is signed
  material.py    the S-N curve, its scatter, and the Eurocode 3 weld detail
  factors.py     everything between a laboratory specimen and the real part
  backend.py     the seam to pyLife (Decision 2), and nothing else
  assessment.py  the judgements: what is refused, what is assumed, what is reported
```

**What this package does not do.** It does not sign anything, and it does not decide
anything an engineer is paid to decide. A `PASSED` verdict says the declared inputs,
summed by the declared rule, give a damage below the declared limit — that and no
more. `assessment.py`'s docstring enumerates the eight judgements that remain with a
qualified engineer and names the field each one is declared in. Decision 5 is not
satisfied by a disclaimer; it is satisfied by that list being real.

**The offline contract.** Nothing here opens a socket, a session or a seat, and
nothing imports the geometry kernel at module load — so an optimiser can build and run
ten thousand assessments in a loop, and the tests run in about a second. The one
external dependency is pyLife, and the package still *imports* without it:
`backend.available()` probes, and `Assessment.run` degrades to `UNMEASURED` naming the
missing install rather than raising.
"""

from app.fatigue.assessment import (
    DAMAGE_PATH,
    LIFE_PATH,
    MINER_RULE,
    Assessment,
    BlockDamage,
    Confidence,
    DamageResult,
    MeanStressPolicy,
    Method,
)
from app.fatigue.backend import (
    FatigueBackend,
    PyLifeBackend,
    available,
    default_backend,
    import_error,
    library_version,
    require,
)
from app.fatigue.errors import BackendUnavailable, FatigueError
from app.fatigue.factors import REQUIRED_FACTORS, Factor, FactorSet, StressConcentration
from app.fatigue.history import (
    Collective,
    CycleBlock,
    LoadHistory,
    SignConvention,
    StressBasis,
)
from app.fatigue.material import SNCurve, WeldDetail
from app.fatigue.sources import require_source

__all__ = [
    "DAMAGE_PATH",
    "LIFE_PATH",
    "MINER_RULE",
    "REQUIRED_FACTORS",
    "Assessment",
    "BackendUnavailable",
    "BlockDamage",
    "Collective",
    "Confidence",
    "CycleBlock",
    "DamageResult",
    "Factor",
    "FactorSet",
    "FatigueBackend",
    "FatigueError",
    "LoadHistory",
    "MeanStressPolicy",
    "Method",
    "PyLifeBackend",
    "SNCurve",
    "SignConvention",
    "StressBasis",
    "StressConcentration",
    "WeldDetail",
    "available",
    "default_backend",
    "import_error",
    "library_version",
    "require",
    "require_source",
]
