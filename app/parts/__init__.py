"""Bought-in standard parts: what they are called, their geometry, their data.

Phase 12.3. Seventy per cent of a real machine is bought, and the agent's job
with a bought-in part is to *place* it and *check* it, never to model it.

Reading order: `types.py` (what a part is, and what "missing engineering data"
means here), then `fasteners.py` (the shipped ISO set and why it is first-party
rather than BOLTS), then `catalogue.py` (lookup, refusal, and the seam a larger
library plugs into), then `bearings.py` (E12.4 — choosing one, and the sharp
line between the standard's arithmetic and the manufacturer's data).

A part's numbers carry the same `Property` record as a material's — value, unit,
status, source — because a proof load and a yield strength are the same kind of
claim. That record lives in `app.solve.materials`, which is lower in the stack.
"""

from app.solve.materials import Property, Source, SourceKind, Status

from .bearings import (
    Bearing,
    BearingCatalogue,
    BearingError,
    BearingKind,
    Duty,
    Refusal,
    Selection,
    select_bearing,
)
from .catalogue import (
    CATALOGUE,
    Catalogue,
    MappingSource,
    PartSource,
    bolt_for_clamp,
    find,
    nut_for,
    search,
    washer_for,
)
from .fasteners import (
    FASTENERS,
    PROPERTY_CLASSES,
    THREADS,
    assembly_preload,
    clamp_length_range_mm,
    clamps,
    hex_bolt,
    hex_nut,
    plain_washer,
    tightening_torque,
)
from .types import (
    EXPECTED_ENGINEERING,
    Designation,
    DesignationError,
    MissingEngineeringData,
    PartKind,
    StandardPart,
    UnknownPart,
)

__all__ = [
    "CATALOGUE",
    "Bearing",
    "BearingCatalogue",
    "BearingError",
    "BearingKind",
    "Duty",
    "Refusal",
    "Selection",
    "select_bearing",
    "EXPECTED_ENGINEERING",
    "FASTENERS",
    "PROPERTY_CLASSES",
    "THREADS",
    "Catalogue",
    "Designation",
    "DesignationError",
    "MappingSource",
    "MissingEngineeringData",
    "PartKind",
    "PartSource",
    "Property",
    "Source",
    "SourceKind",
    "StandardPart",
    "Status",
    "UnknownPart",
    "assembly_preload",
    "bolt_for_clamp",
    "clamp_length_range_mm",
    "clamps",
    "find",
    "hex_bolt",
    "hex_nut",
    "nut_for",
    "plain_washer",
    "search",
    "tightening_torque",
    "washer_for",
]
