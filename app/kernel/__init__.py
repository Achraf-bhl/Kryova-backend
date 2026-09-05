"""The geometry kernel: building real geometry without a licensed workstation.

Master plan Phase 1. The design IR (`app/design/`) compiles a specification into a
`Plan` of registry operations; this executes one against **OCCT** — in-process, free,
headless, deterministic. That is what makes geometry testable in CI, makes optimisation
and self-correction affordable, and removes the licence ceiling.

```
app/kernel/
  errors.py        the shared error hierarchy
  measurement.py   what a measured part reports, and how two backends are compared
  conformance.py   running one plan on two backends and reporting what differs (1.5)
  determinism.py   a stable identity for geometry, so I5 is checkable (1.6)
  occt/            the OCCT backend (see its own docstring for reading order)
```

`measurement.py` sits above the backends on purpose: the CATIA daemon produces the same
payload from a real seat, and the whole two-backend design rests on an assertion being
unable to tell which one it is reading.

Two things to know before changing anything here:

* **The kernel is optional at import time and required at call time.** Everything
  imports on a machine with no OCCT — `app/catia/` keeps the same contract for
  `pywin32` — so the suite still runs for anyone who has not installed a large
  dependency. `available()` is the probe; `require()` is the refusal.
* **Naming has three rules that are not obvious**, all three discovered the hard way and
  all three documented in `occt/naming.py`. Break one and the layer *appears* to work:
  in two of the three cases `TNaming_Selector.Solve()` returns success while resolving
  to nothing at all.
"""

from app.kernel.conformance import ConformanceResult, compare_backends
from app.kernel.determinism import environment, geometry_digest, is_reproducible
from app.kernel.errors import (
    GeometryError,
    KernelError,
    KernelUnavailable,
    MeasurementError,
    NamingError,
    OperationNotSupported,
)
from app.kernel.measurement import (
    CONFORMANCE_TOLERANCE_MM3,
    MM3_KG_PER_M3_TO_KG,
    Detail,
    compare,
    mass_kg,
)
from app.kernel.occt import (
    Feature,
    FeatureLabels,
    NameRegistry,
    OcctRunner,
    PartDocument,
    available,
    import_error,
    occt_version,
    require,
)

__all__ = [
    "CONFORMANCE_TOLERANCE_MM3",
    "ConformanceResult",
    "MM3_KG_PER_M3_TO_KG",
    "Detail",
    "Feature",
    "FeatureLabels",
    "GeometryError",
    "KernelError",
    "KernelUnavailable",
    "MeasurementError",
    "NameRegistry",
    "NamingError",
    "OcctRunner",
    "OperationNotSupported",
    "PartDocument",
    "available",
    "compare",
    "compare_backends",
    "environment",
    "geometry_digest",
    "import_error",
    "is_reproducible",
    "mass_kg",
    "occt_version",
    "require",
]
