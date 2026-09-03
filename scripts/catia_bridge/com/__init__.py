"""Per-workbench COM implementations, mixed into `catia_com.CatiaCom`.

`catia_com.py` covered the original 39 tools in 2 400 lines. The registry is
200, so the rest live here — one module per workbench, each mixing into the same
backend object and using only what `_context` declares.

Order matters in the class bases. `SketcherMixin` must come before the modules
that call `_require_closed` or `_open_sketch`, because those live on it; Python
resolves left to right, so a mixin that provides a helper goes before the ones
that use it. `SketchEditMixin` is therefore after `SketcherMixin`, and
`AssemblyReviewMixin` after `AssemblyMixin` for `_product` and `_component`.
`CatiaCom`'s own methods win over all of them, which is what lets the original
hand-written implementations stay authoritative for the tools they already
covered.
"""

from .assembly import AssemblyMixin
from .assembly_review import AssemblyReviewMixin
from .drafting import DraftingMixin
from .infrastructure import InfrastructureMixin
from .inspection import InspectionMixin
from .knowledge import KnowledgeMixin
from .part_design import PartDesignMixin
from .reference import ReferenceMixin
from .sketch_edit import SketchEditMixin
from .sketcher import SketcherMixin
from .surfaces import SurfacesMixin
from .wireframe import WireframeMixin

#: Every mixin, in method-resolution order.
WORKBENCH_MIXINS = (
    SketcherMixin,
    SketchEditMixin,
    ReferenceMixin,
    PartDesignMixin,
    SurfacesMixin,
    WireframeMixin,
    AssemblyMixin,
    AssemblyReviewMixin,
    DraftingMixin,
    InfrastructureMixin,
    KnowledgeMixin,
    InspectionMixin,
)

__all__ = [
    "WORKBENCH_MIXINS",
    "AssemblyMixin",
    "AssemblyReviewMixin",
    "DraftingMixin",
    "InfrastructureMixin",
    "InspectionMixin",
    "KnowledgeMixin",
    "PartDesignMixin",
    "ReferenceMixin",
    "SketchEditMixin",
    "SketcherMixin",
    "SurfacesMixin",
    "WireframeMixin",
]
