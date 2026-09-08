"""CalculiX, across a subprocess boundary — master plan Phase 6.

Decision 2 says physics is federated and never re-implemented: `app/solve/` is a
correct *component* solver with no contact, no plasticity, no large deformation
and a direct solve that will not survive an assembly, and writing the missing
90% is a decade of specialist work that has already been done and given away.

Decision 4 says how. CalculiX is GPL, so it is invoked as a **separate process
across a file/CLI boundary** — write a deck, run `ccx`, read the results. Never
linked in-process, however convenient. That is a licence obligation first and
good architecture second: a solver that crashes should take a subprocess with it,
not the API.

What is kept, emphatically, is the layer above: `loads.py`, `selection.py` and
`materials.py`. The load-case vocabulary and the *geometric* selector — a face
named by where it is, never by an id that dies on re-export — are what the agent
drives and what makes a load case survive a re-mesh. `deck.py` writes those
existing structures out; it does not restate them.

And the hand-written solver stays, as fast path and as **oracle** (6.5): any
linear static case must agree between the two, and a disagreement is a bug in
this integration rather than a matter of opinion.
"""

from app.solve.calculix.deck import (
    C3D10_EDGES,
    C3D10_MIDSIDE_ORDER,
    DENSITY_KG_M3_TO_TONNE_MM3,
    element_type,
    write_deck,
    write_frame_deck,
    write_frame_model,
)
from app.solve.calculix.diagnose import Diagnosis, diagnose
from app.solve.calculix.elements import (
    EXPANSIONS,
    ElementChoice,
    Expansion,
    choose_element,
)
from app.solve.calculix.frd import (
    FrdFile,
    displacements,
    nodal_stress_tensor,
    parse_frd,
    von_mises_from_tensor,
)
from app.solve.calculix.run import (
    CalculiXUnavailable,
    CcxRun,
    find_ccx,
    require_ccx,
    run_ccx,
)
from app.solve.calculix.solver import CalculiXSolver, element_von_mises

__all__ = [
    "C3D10_EDGES",
    "CalculiXSolver",
    "CalculiXUnavailable",
    "CcxRun",
    "Diagnosis",
    "EXPANSIONS",
    "ElementChoice",
    "Expansion",
    "choose_element",
    "C3D10_MIDSIDE_ORDER",
    "DENSITY_KG_M3_TO_TONNE_MM3",
    "FrdFile",
    "diagnose",
    "displacements",
    "element_type",
    "element_von_mises",
    "find_ccx",
    "nodal_stress_tensor",
    "parse_frd",
    "require_ccx",
    "run_ccx",
    "von_mises_from_tensor",
    "write_deck",
    "write_frame_deck",
    "write_frame_model",
]
