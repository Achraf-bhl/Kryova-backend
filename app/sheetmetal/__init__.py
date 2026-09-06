"""Sheet metal: bends, flat patterns, and whether a press brake can make the part.

Phase 17.3, and a **sequencing exception** — it belongs to Phase 17 with the
rest of manufacturing output and runs with Era IV, because the mission ladder
promises M3 (an enclosure) in Era IV and M5 (a stamping press) in Era V and both
are sheet metal long before they are drawings.

Reading order:

1. `kfactor.py` — where the neutral axis sits and, the point of the module,
   where the number came from. Read this first: everything else is arithmetic
   and this is the judgement.
2. `material.py` — one sheet, one thickness, and the radius below which the
   grade cracks, with a source on the same terms as K.
3. `bend.py` — `BA`, `SB`, `BD`, the three mould-line conventions, and why a
   180 degree bend has no setback.
4. `unfold.py` — a folded part as a tree of rectangular flanges, and the blank
   it flattens to. Its docstring lists exactly what can and cannot be unfolded.
5. `formability.py` — the three limits that decide whether it can be formed,
   each refusing rather than warning.

The package runs offline with no geometry kernel, no solver and no database —
the property `app/design/` keeps and for the same reason: a flat pattern is
arithmetic, and arithmetic that needs 166 MB of OCP to test is arithmetic
nobody tests.

**Nothing here reaches into `app/manufacture/`.** The flat pattern is offered
as closed polylines in millimetres (`unfold.Polyline`, structurally the alias
`app.manufacture.drawing` declares) plus bend lines, holes and an extent, so a
flat-pattern drawing can be produced from it later without this package
depending on drawings. `unfold.py`'s docstring states that interface.
"""

from app.sheetmetal.bend import (
    STRAIGHT_ANGLE_DEG,
    Bend,
    BendDirection,
    LengthConvention,
    bend_allowance_mm,
    bend_deduction_mm,
    setback_mm,
)
from app.sheetmetal.errors import (
    BendError,
    FormabilityError,
    SheetMetalError,
    UnfoldError,
)
from app.sheetmetal.formability import (
    AIR_BEND_DIE_RATIO,
    HOLE_EDGE_TO_TANGENT_FACTOR,
    Finding,
    FormabilityReport,
    air_bend_die_opening_mm,
    check_part,
    minimum_flange_mm,
)
from app.sheetmetal.kfactor import (
    DIN_6935_SOURCE,
    MACHINERYS_HANDBOOK,
    MAX_K,
    Band,
    Basis,
    KFactor,
    KFactorTable,
    MaterialFamily,
    assumed,
    din6935,
    machinerys_handbook,
    measured,
    unstated,
)
from app.sheetmetal.material import (
    SHIPPED_GRADES,
    MinimumBendRadius,
    SheetMaterial,
    sheet_material,
)
from app.sheetmetal.unfold import (
    Edge,
    Flange,
    FlatBendLine,
    FlatFace,
    FlatHole,
    FlatPattern,
    Hole,
    Joint,
    Polyline,
    SheetMetalPart,
    unfold,
)

__all__ = [
    "AIR_BEND_DIE_RATIO",
    "DIN_6935_SOURCE",
    "HOLE_EDGE_TO_TANGENT_FACTOR",
    "MACHINERYS_HANDBOOK",
    "MAX_K",
    "SHIPPED_GRADES",
    "STRAIGHT_ANGLE_DEG",
    "Band",
    "Basis",
    "Bend",
    "BendDirection",
    "BendError",
    "Edge",
    "Finding",
    "Flange",
    "FlatBendLine",
    "FlatFace",
    "FlatHole",
    "FlatPattern",
    "FormabilityError",
    "FormabilityReport",
    "Hole",
    "Joint",
    "KFactor",
    "KFactorTable",
    "LengthConvention",
    "MaterialFamily",
    "MinimumBendRadius",
    "Polyline",
    "SheetMaterial",
    "SheetMetalError",
    "SheetMetalPart",
    "UnfoldError",
    "air_bend_die_opening_mm",
    "assumed",
    "bend_allowance_mm",
    "bend_deduction_mm",
    "check_part",
    "din6935",
    "machinerys_handbook",
    "measured",
    "minimum_flange_mm",
    "setback_mm",
    "sheet_material",
    "unfold",
    "unstated",
]
