"""The tools an MCP client is offered, and what it is told about them (ROAD_TO_10 9.7, E23.3).

The agent's own registry is ~246 tools and is shaped for Kryova's model: retrieval narrows it per
turn and the system prompt teaches it. An external MCP client has none of that. It sees every
`tools/list` entry on every turn, spends its context on 212 CATIA operations, and has no retrieval
to find the 12 it needs -- so the surface offered over MCP is a **curated set of about thirty**, each
with the description the tool already carries (one source, so a reworded tool changes both surfaces).

What is in it, and what is deliberately not:

* **In:** reading and recording a design, building it, the core part-modelling operations (sketch,
  pad, pocket, hole, fillet, chamfer, shell, patterns, delete a feature), measuring, capturing a
  view, exporting STEP, checking the part, running a structural or thermal simulation and reading
  it back, materials, projects, and the documentation search.
* **Out, by design:** anything destructive that cannot be undone from a protocol (deleting a
  project or a simulation), the CATIA user-interface driver (`catia_run_command`, `catia_press_key`
  and friends -- they wedge a seat when misused), approvals and the plan (`request_approval`,
  `plan_work`, `update_task` belong to Kryova's own agent loop, and an MCP client cannot supply the
  approval a gate waits for), and the long tail of `catia_*` operations. `KRYOVA_MCP_TOOL_SET=full`
  restores the whole vocabulary for a client that wants it.

The set is data, and `tests/test_mcp_tools.py` holds it to three claims: every name is a real tool,
it stays between 20 and 40, and nothing in it is on the withheld list.
"""

from __future__ import annotations

from typing import Final

from app.verify.standards import NOT_VALIDATED

CURATED: Final[tuple[str, ...]] = (
    # Design: read it, change one parameter, record or rebuild it, see how it got here.
    "read_design",
    "set_design_parameter",
    "record_design",
    "build_design",
    "design_history",
    # Modelling: the operations most parts are made of.
    "catia_new_part",
    "catia_sketch_create",
    "catia_sketch_rectangle",
    "catia_sketch_circle",
    "catia_sketch_polyline",
    "catia_pad",
    "catia_pocket",
    "catia_hole",
    "catia_fillet",
    "catia_chamfer",
    "catia_shell",
    "catia_pattern_circular",
    "catia_pattern_rectangular",
    "catia_delete_feature",
    "catia_update",
    # Looking at it and getting it out.
    "catia_list_features",
    "catia_measure",
    "catia_capture_view",
    "catia_export_step",
    "catia_status",
    "check_part",
    # Analysis.
    "run_simulation",
    "run_thermal_simulation",
    "get_simulation",
    "list_simulations",
    "wait_for_simulation",
    "list_materials",
    # Projects and the manuals.
    "list_projects",
    "get_project",
    "create_project",
    "list_geometry",
    "search_documentation",
)

#: Named so a test can fail when one of them slips into `CURATED`.
WITHHELD: Final[tuple[str, ...]] = (
    "delete_project",
    "delete_simulation",
    "request_approval",
    "plan_work",
    "update_task",
    "catia_run_command",
    "catia_press_key",
    "catia_dialog_action",
    "catia_fill_dialog",
    "catia_restore",
)

MIN_CURATED = 20
MAX_CURATED = 40

TOOL_SETS: Final[tuple[str, ...]] = ("curated", "full")

#: What `initialize` hands the client. The three rules an outside caller most needs and a
#: Kryova-trained model never has to be told: units, consent, and the honesty of a number.
INSTRUCTIONS: Final[str] = (
    "Kryova designs, analyses and documents machine parts. Every tool acts on the one "
    "conversation named in this endpoint's URL.\n"
    "UNITS are millimetres, newtons and megapascals everywhere and nothing converts: lengths and "
    "displacements in mm, forces in N, stress and Young's modulus in MPa, density in kg/m3, and "
    "mass comes back in kilograms. Send a value in any other unit and it will be read as these.\n"
    "CONSENT: a tool that changes state refuses in words unless the request carries "
    "`_meta[\"kryova/allowMutations\"]: true`. Read-only tools never need it. Ask the person "
    "before setting it; do not set it to get past a refusal.\n"
    "NUMBERS: a simulation result says whether its mesh was converged. A value marked "
    "`single-grid` or not converged is not an answer: do not quote it as one. Ask for a "
    "convergence study (`grids: 3`) and quote the converged value, or say that it is unverified. "
    "A measurement that could not be made comes back UNMEASURED, which is not a pass.\n"
    "SCOPE: " + NOT_VALIDATED
)
