"""Per-workbench COM implementations, mixed into `catia_com.CatiaCom`.

`catia_com.py` covered the original 39 tools in 2 400 lines. The registry is
200 and growing, so the rest live here — one module per workbench, each mixing
into the same backend object and using only what `_context` declares.

Order matters in the class bases. `SketcherMixin` must come before the modules
that call `_require_closed`, because that method lives on it; Python resolves
left to right, so a mixin that provides a helper goes before the ones that use
it. `CatiaCom`'s own methods win over all of them, which is what lets the
original hand-written implementations stay authoritative for the tools they
already covered.
"""

from .inspection import InspectionMixin
from .knowledge import KnowledgeMixin
from .part_design import PartDesignMixin
from .reference import ReferenceMixin
from .sketcher import SketcherMixin
from .surfaces import SurfacesMixin
from .wireframe import WireframeMixin

#: Every mixin, in method-resolution order.
WORKBENCH_MIXINS = (
    SketcherMixin,
    ReferenceMixin,
    PartDesignMixin,
    SurfacesMixin,
    WireframeMixin,
    KnowledgeMixin,
    InspectionMixin,
)

__all__ = [
    "WORKBENCH_MIXINS",
    "InspectionMixin",
    "KnowledgeMixin",
    "PartDesignMixin",
    "ReferenceMixin",
    "SketcherMixin",
    "SurfacesMixin",
    "WireframeMixin",
]
