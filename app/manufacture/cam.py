"""A machining plan: stock, operations from the part's features, cutters, fixturing -- E17 task 4.

`dropcutter.py` answers where a cutter can go. This module answers the questions before and
around that, from what the product already knows about a part:

* **Stock.** The smallest block from the caller's stock list that holds the part plus the
  caller's machining allowance on every face. Sized off the oriented bounding box when the
  part carries one (what stock to buy, per `kernel/contract.py`) and off the axis-aligned
  box otherwise, and the plan says which. A stock list and an allowance are a supplier's and
  a shop's, so both are the caller's with their sources; with no list, the plan gives the
  minimum block and chooses nothing.
* **Operations.** One per feature the part was built with, from the tool name: a pocket is
  pocketed, a hole drilled, a revolved shaft turned, a fillet finished with a ball or corner
  cutter. A feature with no machining meaning (a mirror, a pattern of something already
  listed) is listed as such rather than dropped, so the plan accounts for every feature.
* **Cutters.** A pocket's corner can be no tighter than the cutter's radius, so the largest
  cutter a pocket may use is twice the part's measured minimum concave radius, and the plan
  picks the largest one in the caller's tool list that fits. A radius that was never scanned
  leaves the choice open and says the curvature scan is needed.
* **Fixturing.** The 3-2-1 locating principle over the part's datum scheme
  (`app.rules.gdt`): the primary datum feature seats on three points, the secondary on two,
  the tertiary on one, and the clamps push toward the locators. Those counts are the
  geometry of constraining a rigid body's six freedoms, not a shop's preference. With no
  datum scheme the plan says the part cannot be located to its own tolerancing.
* **A finishing raster**, as drop-cutter points along parallel lines at the caller's
  stepover, for a cutter the caller names.

**What it does not produce**: feeds and speeds (a function of the machine, the tool
supplier and the material, all the caller's), setups by approach direction (no
per-feature access direction is recorded yet), and G-code (a machine's post-processor).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

from app.manufacture.dropcutter import CamError, Cutter, as_triangles, drop
from app.rules.gdt import DatumScheme
from app.rules.processes import Limit

#: Tool name -> (operation, what it needs). Keyed on the operation registry's names.
OPERATIONS: Final[Mapping[str, tuple[str, str]]] = {
    "catia_pad": ("contour", "profile the outside to the pad's sketch"),
    "catia_pad_drafted_filleted": ("contour", "profile with a tapered or corner-radius cutter"),
    "catia_pocket": ("pocket", "clear the pocket; corner radius limits the cutter"),
    "catia_slot": ("slot", "a cutter no wider than the slot"),
    "catia_rib": ("contour", "follow the rib's path; a 3-axis raster or 5-axis if it twists"),
    "catia_groove": ("turn", "a revolved cut, turned or interpolated"),
    "catia_shaft": ("turn", "a revolved body, turned from bar"),
    "catia_hole": ("drill", "drill to depth; ream or bore where the fit needs it"),
    "catia_hole_at": ("drill", "drill to depth; ream or bore where the fit needs it"),
    "catia_hole_pattern": ("drill", "one drill cycle per hole in the pattern"),
    "catia_thread": ("tap", "tap or thread-mill after drilling"),
    "catia_shell": ("pocket", "hollowing: a pocket whose corner radius limits the cutter"),
    "catia_shell_faces": ("pocket", "hollowing: a pocket whose corner radius limits the cutter"),
    "catia_thickness": ("face", "face to the stated thickness"),
    "catia_fillet": ("finish", "a ball or corner-radius cutter, or a raster finishing pass"),
    "catia_fillet_edges": ("finish", "a ball or corner-radius cutter, or a raster finishing pass"),
    "catia_fillet_face": ("finish", "a ball or corner-radius cutter, or a raster finishing pass"),
    "catia_fillet_variable": ("finish", "a raster or 5-axis finishing pass"),
    "catia_fillet_tritangent": ("finish", "a ball cutter finishing pass"),
    "catia_chamfer": ("chamfer", "a chamfer mill or a tilted pass"),
    "catia_draft": ("finish", "a tapered cutter or a tilted pass"),
}

#: Operations whose cutter size is bounded by the part's tightest inside corner.
_CORNER_BOUND: Final = frozenset({"pocket", "slot"})

#: Locating points a datum feature takes, by precedence: the 3-2-1 principle.
LOCATING_POINTS: Final = (3, 2, 1)


@dataclass(frozen=True)
class StockBlock:
    size_mm: tuple[float, float, float]
    source: str

    def fits(self, needed: Sequence[float]) -> bool:
        return all(s >= n for s, n in zip(sorted(self.size_mm), sorted(needed), strict=True))

    @property
    def volume_mm3(self) -> float:
        return self.size_mm[0] * self.size_mm[1] * self.size_mm[2]


@dataclass(frozen=True)
class Operation:
    feature_tool: str
    kind: str
    how: str
    cutter: Cutter | None = None
    note: str = ""


@dataclass(frozen=True)
class Locator:
    datum: str
    feature: str
    points: int


@dataclass(frozen=True)
class MachiningPlan:
    stock_needed_mm: tuple[float, float, float]
    stock_basis: str
    stock: StockBlock | None
    removed_volume_mm3: float | None
    operations: tuple[Operation, ...]
    locators: tuple[Locator, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_needed_mm": list(self.stock_needed_mm),
            "stock_basis": self.stock_basis,
            "stock": None
            if self.stock is None
            else {"size_mm": list(self.stock.size_mm), "source": self.stock.source},
            "removed_volume_mm3": self.removed_volume_mm3,
            "operations": [
                {
                    "feature_tool": op.feature_tool,
                    "kind": op.kind,
                    "how": op.how,
                    "cutter": None
                    if op.cutter is None
                    else {
                        "shape": op.cutter.shape.value,
                        "diameter_mm": op.cutter.diameter_mm,
                        "source": op.cutter.source,
                    },
                    "note": op.note,
                }
                for op in self.operations
            ],
            "fixturing": [
                {"datum": loc.datum, "feature": loc.feature, "locating_points": loc.points}
                for loc in self.locators
            ],
            "notes": list(self.notes),
            "not_produced": [
                "feeds and speeds",
                "setups by approach direction",
                "G-code (a machine's post-processor)",
            ],
        }


def _read(measurements: Mapping[str, Any], path: str) -> Any:
    node: Any = measurements
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def plan(
    features: Iterable[str],
    measurements: Mapping[str, Any],
    *,
    allowance: Limit,
    stock_list: Sequence[StockBlock] = (),
    cutters: Sequence[Cutter] = (),
    datums: DatumScheme | None = None,
) -> MachiningPlan:
    """A machining plan for a part built with these feature tools and measured so."""
    if allowance.value < 0.0:
        raise CamError(f"A machining allowance of {allowance.value} mm removes material that is not there.")
    notes: list[str] = []

    oriented = _read(measurements, "oriented_bounding_box_mm.size")
    axis = _read(measurements, "bounding_box_mm.size")
    if oriented is not None:
        size, basis = oriented, "oriented bounding box (approximated: the orientation is a search)"
    elif axis is not None:
        size, basis = axis, "axis-aligned bounding box; an oriented box may need less stock"
    else:
        raise CamError(
            "The measurements carry no bounding box, so there is nothing to size stock from. "
            "Measure the part first."
        )
    needed = tuple(float(s) + 2.0 * allowance.value for s in size)
    assert len(needed) == 3
    notes.append(f"allowance {allowance.value:g} mm per face, from {allowance.source}")

    chosen: StockBlock | None = None
    if stock_list:
        fitting = [block for block in stock_list if block.fits(needed)]
        if fitting:
            chosen = min(fitting, key=lambda block: block.volume_mm3)
        else:
            notes.append("no block in the stock list holds the part with its allowance")
    else:
        notes.append("no stock list given, so the minimum block is stated and none is chosen")

    volume = _read(measurements, "volume_mm3")
    removed = None if chosen is None or volume is None else chosen.volume_mm3 - float(volume)

    radius = _read(measurements, "minimum_concave_radius_mm")
    operations: list[Operation] = []
    for tool in features:
        entry = OPERATIONS.get(tool)
        if entry is None:
            operations.append(
                Operation(tool, "none", "no machining of its own", note="listed so every feature is accounted for")
            )
            continue
        kind, how = entry
        cutter: Cutter | None = None
        note = ""
        if kind in _CORNER_BOUND:
            if radius is None:
                note = "cutter not chosen: the minimum concave radius was not measured (curvature scan)"
            else:
                largest = 2.0 * float(radius)
                fitting_cutters = [c for c in cutters if c.diameter_mm <= largest + 1e-9]
                if fitting_cutters:
                    cutter = max(fitting_cutters, key=lambda c: c.diameter_mm)
                    note = f"largest cutter at most {largest:g} mm, twice the tightest inside corner"
                else:
                    note = (
                        f"no cutter in the list is {largest:g} mm or smaller; the corner cannot be "
                        "milled with these tools"
                    )
        operations.append(Operation(tool, kind, how, cutter, note))

    locators: list[Locator] = []
    if datums is None or not datums.datums:
        notes.append(
            "no datum scheme: the part cannot be located to its own tolerancing, so fixturing "
            "is not planned"
        )
    else:
        for datum, points in zip(datums.datums, LOCATING_POINTS, strict=False):
            locators.append(Locator(datum.letter, datum.feature, points))
        if len(datums.datums) > len(LOCATING_POINTS):
            notes.append("datums beyond the tertiary take no locating points")
        if len(locators) < len(LOCATING_POINTS):
            notes.append(
                f"{len(locators)} datum(s) lock {sum(loc.points for loc in locators)} of six freedoms; "
                "the rest are held by the clamps alone"
            )

    return MachiningPlan(
        stock_needed_mm=needed,  # type: ignore[arg-type]
        stock_basis=basis,
        stock=chosen,
        removed_volume_mm3=removed,
        operations=tuple(operations),
        locators=tuple(locators),
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class Raster:
    cutter: Cutter
    stepover_mm: float
    sample_mm: float
    lines: tuple[tuple[tuple[float, float, float], ...], ...]


def raster(
    triangles: Any,
    cutter: Cutter,
    *,
    stepover_mm: float,
    sample_mm: float,
    floor_z_mm: float,
) -> Raster:
    """Parallel-to-x finishing lines, alternating direction, as cutter-tip points."""
    tris = as_triangles(triangles)
    if not (0.0 < stepover_mm < math.inf and 0.0 < sample_mm < math.inf):
        raise CamError("A raster needs a positive stepover and sample spacing.")
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    ys = np.arange(lo[1], hi[1] + stepover_mm * 0.5, stepover_mm)
    xs = np.arange(lo[0], hi[0] + sample_mm * 0.5, sample_mm)
    if len(xs) * len(ys) > 2_000_000:
        raise CamError("That raster is too many drop-cutter queries; increase the spacing.")
    lines = []
    for row, y in enumerate(ys):
        order = xs if row % 2 == 0 else xs[::-1]
        lines.append(
            tuple(
                (float(x), float(y), drop(tris, cutter, float(x), float(y), floor_z_mm=floor_z_mm))
                for x in order
            )
        )
    return Raster(cutter=cutter, stepover_mm=stepover_mm, sample_mm=sample_mm, lines=tuple(lines))


__all__ = [
    "LOCATING_POINTS",
    "OPERATIONS",
    "Locator",
    "MachiningPlan",
    "Operation",
    "Raster",
    "StockBlock",
    "plan",
    "raster",
]
