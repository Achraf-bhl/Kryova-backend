"""Product structure, decomposition and interface contracts — master plan Phase 14.

The level above `app.design`. A `DesignSpec` describes one part as a specification that
is compiled; this describes a *product* the same way, and for the same reason: a machine
assembled from a graph that is regenerated has no downstream edit to break, where an
assembly tree that is edited in place has the topological naming problem one level up.

Everything above rung 3 of the mission ladder is more than one part, gate **G3** needs
assemblies with interface contracts, and M2 onward are all assemblies. This is the
package that makes those sayable.

```
app/assembly/
  errors.py       what can go wrong: a malformed structure, a malformed contract
  placement.py    compositions of app.dynamics.pose.Frame, and a conservative box
  structure.py    14.1 — components, instances, occurrences, BOM, where-used
  contracts.py    14.2/14.3/14.4 — the boundary between two things, as an assertion
  clash.py        does anything hit anything, and what was actually looked at
  mass.py         what it weighs, where its centre is, and what was left out
```

Reading order: `structure.py` first (what a product *is* here), then `contracts.py`
(the subject of the phase), then `clash.py` and `mass.py`, which are two consumers of
the walk and are independent of each other.

**Four things to know before changing anything here.**

* **A bolt used forty times is one component and forty occurrences.** Occurrences are
  computed by a walk and never stored. Break that and a 5,000-part machine is 5,000
  solids in memory, and "change that bolt" becomes a forty-place edit.
* **An occurrence path's number is declared, never positional.** `leg.2` means the leg
  the author called 2, so inserting a leg at the head of the list renumbers nothing.
  This is `app.design.names`' rule, one level up, and it is the reason a clash exclusion
  or a mass budget written last week still points at the same part.
* **There is exactly one verdict vocabulary and it is not in this package.** An
  interface claim is an `app.design.assertions.Assertion` and comes back `PASSED`,
  `FAILED` or `UNMEASURED`. A contract adds *who* — both parties, by name — and nothing
  else.
* **Anything not checked is counted and named.** A clash check that skipped pairs, and a
  mass that omitted an occurrence, are both wrong in the direction that passes: the
  machine looks roomier and lighter than it is. So `ClashReport.complete` and
  `MassRollup.complete` gate the headline numbers, and the partial ones go out under
  different names with an `UNAVAILABLE` provenance record saying what is missing.

**The kernel is imported lazily and only where geometry is genuinely needed**
(`clash.occt_bounds`, `clash.occt_measurer`, `mass.from_document`, and the
`to_payload` methods). Importing `app.kernel` executes its `__init__`, which pulls in
~166 MB of OCP; keeping it out of module import is what lets the structure and contract
tests run offline in milliseconds — the property `app.design` keeps, for the same reason.

**What Phase 14 asks for and this does not have, stated plainly.** 14.1's *effectivity*
(a component valid from serial 400) is not implemented — `Component.revision` is free
text and nothing selects on it. 14.5, concurrency and locking for several agents on one
product, is not started: nothing here takes a lock, and two callers editing one
structure will simply produce two structures.
"""

from app.assembly.clash import (
    ClashFinding,
    ClashReport,
    SkippedPair,
    find_clashes,
    occt_bounds,
    occt_measurer,
    touching_components,
)
from app.assembly.contracts import (
    ContractResult,
    Impact,
    Interface,
    Violation,
    affected,
    bind_both,
    bind_into,
    check,
    check_all,
    measurements,
)
from app.assembly.errors import AssemblyError, ContractError, StructureError
from app.assembly.mass import MassRollup, MissingMass, WeighedOccurrence, roll_up
from app.assembly.placement import Box, at, compose, invert, relative, turned
from app.assembly.structure import (
    BomLine,
    Component,
    Instance,
    Occurrence,
    ProductStructure,
    StructureBuilder,
    spread,
)

__all__ = [
    "AssemblyError",
    "BomLine",
    "Box",
    "ClashFinding",
    "ClashReport",
    "Component",
    "ContractError",
    "ContractResult",
    "Impact",
    "Instance",
    "Interface",
    "MassRollup",
    "MissingMass",
    "Occurrence",
    "ProductStructure",
    "SkippedPair",
    "StructureBuilder",
    "StructureError",
    "Violation",
    "WeighedOccurrence",
    "affected",
    "at",
    "bind_both",
    "bind_into",
    "check",
    "check_all",
    "compose",
    "find_clashes",
    "invert",
    "measurements",
    "occt_bounds",
    "occt_measurer",
    "relative",
    "roll_up",
    "spread",
    "touching_components",
    "turned",
]
