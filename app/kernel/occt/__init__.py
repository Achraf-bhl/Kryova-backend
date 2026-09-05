"""The OCCT backend: the design IR compiled into real geometry, headless and free.

Read in this order:

* `binding` — the one place OCP is imported, and the one place its absence is handled;
* `topology` — walking a shape (the hot path);
* `metrology` — measuring one (the expensive path);
* `naming` — persistent topological naming, and the three rules that govern it;
* `selectors` — resolving the registry's selector words against real geometry;
* `document` — a part mid-construction, and the label tree that survives regeneration;
* `operations/` — one module per domain, assembled into the handler table;
* `runner` — the `CallRunner` the executor drives.

Nothing here imports `app.design`. The dependency runs one way: the design layer knows
about `CallRunner`, and this implements it.
"""

from app.kernel.occt.binding import DISTRIBUTION, available, import_error, occt_version, require
from app.kernel.occt.document import Feature, PartDocument
from app.kernel.occt.naming import (
    FeatureLabels,
    NameRegistry,
    allocate_feature_labels,
    evolution_of,
    record_derived,
    record_primitive,
)
from app.kernel.occt.runner import OcctRunner
from app.kernel.occt.selectors import select_edges

__all__ = [
    "DISTRIBUTION",
    "Feature",
    "FeatureLabels",
    "NameRegistry",
    "OcctRunner",
    "PartDocument",
    "allocate_feature_labels",
    "available",
    "evolution_of",
    "import_error",
    "occt_version",
    "record_derived",
    "record_primitive",
    "require",
    "select_edges",
]
