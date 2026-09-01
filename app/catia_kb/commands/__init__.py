"""The command inventory, one module per domain.

Split by domain rather than kept in one file because these are data modules: a
single file holding every CATIA command would be tens of thousands of lines and
unreviewable in a diff. The split follows the Start menu, which is also how a
user thinks about where a command lives.

Nothing here imports anything but `types`, so a data module can never
accidentally depend on the registry that assembles it.
"""

from __future__ import annotations

from app.catia_kb.commands import (
    analysis,
    assembly,
    automation,
    composites,
    dmu,
    drafting,
    knowledgeware,
    machining,
    part_design,
    sheet_metal,
    sketcher,
    surfaces,
    systems,
)
from app.catia_kb.types import Section

#: Import order is display order in coverage reports and nothing else; the
#: registry indexes by key, so two modules cannot shadow one another silently.
SECTIONS: tuple[Section, ...] = (
    sketcher.SECTION,
    part_design.SECTION,
    surfaces.SECTION,
    assembly.SECTION,
    sheet_metal.SECTION,
    drafting.SECTION,
    analysis.SECTION,
    dmu.SECTION,
    composites.SECTION,
    systems.SECTION,
    machining.SECTION,
    knowledgeware.SECTION,
    automation.SECTION,
)

__all__ = ["SECTIONS"]
