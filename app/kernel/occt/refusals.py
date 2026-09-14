"""Why the open kernel does not implement an operation, one reason per operation.

Master plan E1 task 3: *each registry operation gets an OCCT implementation or
an explicit, reasoned refusal*. Until 2026-09-14 the second half did not exist.
An operation missing from `HANDLERS` raised `OperationNotSupported(tool)` with
no reason, and the dispatcher wrapped every one of them in the same sentence:
"not implemented in the open kernel yet". That sentence is true. It does not
say whether the gap is waiting for code, or whether the operation means nothing
without a CATIA window.

**Three different reasons, and they are not interchangeable.**

* **The operation drives CATIA itself**: a dialog, a menu, the selection, a
  file on the workstation. The open kernel has no interface and no files, so
  there is nothing to build, ever.
* **Something else in this product does the job**, in a different shape. A
  drawing comes from `app/manufacture/`, a relation between parameters from the
  design IR, taking a feature back from `catia_delete_feature`. The reason names
  that somewhere else.
* **Nobody has needed it yet.** Decision 1 is explicit: an operation is added to
  the OCCT backend "only when a test, a sweep or an optimisation needs one, never
  for coverage". So "not built" is the policy, not an oversight. Where an
  implemented operation gets the same part another way, the reason says which.

**A reason may only name a tool the open kernel serves.** A refusal that sends
the agent to an operation it will also be refused costs a turn for nothing, and
that is how the agent loses a conversation. `tests/test_kernel_refusals.py`
reads every `catia_` name in every reason and checks it is in `HANDLERS` or
`backends.LOCALLY_SERVED`.

**Every declared operation is in exactly one place**: `HANDLERS`,
`LOCALLY_SERVED`, or here. An operation added to the registry without an
implementation fails that test until somebody writes down why.
"""

from __future__ import annotations

from typing import Final

#: Said of every operation that drives CATIA's own interface.
_CATIA_INTERFACE: Final = (
    "It drives CATIA's own interface (a dialog, a menu, the keyboard, the selection "
    "or the 3D window), and the open kernel has no interface to drive"
)

#: Said of every Drafting operation.
_DRAWING: Final = (
    "It edits a CATIA drawing document. On the open kernel there is no drawing "
    "document: a drawing is generated from the finished part by app/manufacture "
    "(views, dimensions and the DXF sheet), not built view by view"
)

#: Said of Knowledge Advisor relations.
_RELATIONS: Final = (
    "It writes a Knowledge Advisor relation into a CATPart. On the open kernel a part "
    "built call by call has no relations; its dimensions are its build log "
    "(catia_list_parameters, catia_set_parameter), and a formula between parameters "
    "belongs in a design spec, where app/design/params evaluates it"
)

#: Said of an operation nobody has needed, where nothing implemented does the job.
_NOT_NEEDED: Final = (
    "No test, sweep or optimisation has needed it on the open kernel yet, and an "
    "operation is added only when one does (master plan Decision 1)"
)


def _not_needed(instead: str = "") -> str:
    return f"{_NOT_NEEDED}. {instead}" if instead else _NOT_NEEDED


#: Operation name → why the open kernel does not implement it.
REASONS: Final[dict[str, str]] = {
    # -- CATIA's interface ---------------------------------------------------
    "catia_capture_view": (
        f"{_CATIA_INTERFACE}. It screenshots CATIA's window; the open kernel's part is "
        "drawn by app/render, which is not this tool"
    ),
    "catia_describe_dialog": _CATIA_INTERFACE,
    "catia_dialog_action": _CATIA_INTERFACE,
    "catia_fill_dialog": _CATIA_INTERFACE,
    "catia_graphic_properties": _CATIA_INTERFACE,
    "catia_list_commands": _CATIA_INTERFACE,
    "catia_press_key": _CATIA_INTERFACE,
    "catia_run_command": _CATIA_INTERFACE,
    "catia_select": _CATIA_INTERFACE,
    "catia_switch_workbench": _CATIA_INTERFACE,
    "catia_view_control": _CATIA_INTERFACE,
    # -- CATIA's documents and files ------------------------------------------
    "catia_open_document": (
        "It opens a CATIA file on the workstation. The open kernel holds one part in "
        "memory per conversation and has no files to open; catia_new_part starts one"
    ),
    "catia_close_document": (
        "It saves and closes a CATIA window. The open kernel's part is in memory and "
        "has no window; catia_assembly_component puts the open part away"
    ),
    "catia_checkpoint": (
        "A checkpoint is a saved CATPart the server stores, and the open kernel's part "
        "is in memory with no file to save. To take back one feature, use "
        "catia_delete_feature"
    ),
    "catia_restore": (
        "It reopens a saved CATPart, and the open kernel keeps no saved copies to roll "
        "back to. To take back one feature, use catia_delete_feature"
    ),
    "catia_import": _not_needed(
        "An uploaded CAD file reaches the solver as a geometry version of the project, "
        "not through the part being built"
    ),
    "catia_export": _not_needed(
        "catia_export_step writes this part as STEP, which is what the solver reads"
    ),
    "catia_status": (
        "It reports the state of a CATIA seat, and the server answers it before any "
        "backend is asked, so no kernel implements it"
    ),
    # -- Drafting --------------------------------------------------------------
    "catia_annotation_add": _DRAWING,
    "catia_datum_add": _DRAWING,
    "catia_dimension_add": _DRAWING,
    "catia_dimension_chain": _DRAWING,
    "catia_dimension_generate": _DRAWING,
    "catia_drawing_create": _DRAWING,
    "catia_drawing_update": _DRAWING,
    "catia_dressup_add": _DRAWING,
    "catia_sheet_add": _DRAWING,
    "catia_sheet_frame": _DRAWING,
    "catia_table_add": _DRAWING,
    "catia_tolerance_add": _DRAWING,
    "catia_view_add": _DRAWING,
    "catia_view_align": _DRAWING,
    "catia_view_properties": _DRAWING,
    # -- Knowledge Advisor ------------------------------------------------------
    "catia_check_create": _RELATIONS,
    "catia_design_table_activate": _RELATIONS,
    "catia_design_table_create": _RELATIONS,
    "catia_formula_create": _RELATIONS,
    "catia_knowledge_report": _RELATIONS,
    "catia_measure_publish": _RELATIONS,
    "catia_parameter_create": _RELATIONS,
    "catia_parameter_set_create": _RELATIONS,
    "catia_rule_create": _RELATIONS,
    "catia_drive_to": (
        "CATIA runs this loop inside the seat. On the open kernel the same loop is "
        "catia_set_parameter followed by catia_measure, repeated, and every step of it "
        "is visible in the conversation"
    ),
    # -- Assembly Design ----------------------------------------------------------
    "catia_component_add": _not_needed(
        "catia_assembly_component adds the part being built to the assembly"
    ),
    "catia_component_move": _not_needed(
        "catia_assembly_place positions a component by an explicit transform"
    ),
    "catia_component_fix": _not_needed(
        "A component on the open kernel stays where catia_assembly_place put it; there "
        "is no constraint solver to move it"
    ),
    "catia_constrain": _not_needed(
        "There is no assembly constraint solver on the open kernel; catia_assembly_place "
        "positions a component by an explicit transform"
    ),
    "catia_constraint_set_active": _not_needed(
        "There are no assembly constraints on the open kernel to switch off"
    ),
    "catia_constraint_update": _not_needed(
        "There are no assembly constraints on the open kernel to solve"
    ),
    "catia_component_multi_instantiate": _not_needed(),
    "catia_component_properties": _not_needed(),
    "catia_component_remove": _not_needed(),
    "catia_component_replace": _not_needed(),
    "catia_assembly_feature": _not_needed(),
    "catia_scene_explode": _not_needed(),
    # -- Part Design ------------------------------------------------------------
    "catia_affinity": _not_needed(
        "catia_scale scales uniformly, or along the normal of one plane"
    ),
    "catia_feature_activate": _not_needed(
        "To take a feature out of the part, use catia_delete_feature"
    ),
    "catia_feature_reorder": _not_needed(),
    "catia_fillet_face": _not_needed(
        "catia_fillet_edges rounds the edge two faces share"
    ),
    "catia_hole_pattern": _not_needed(
        "Drill one hole with catia_hole_at and repeat it with catia_pattern_user, "
        "catia_pattern_rectangular or catia_pattern_circular"
    ),
    "catia_multi_section_solid": _not_needed(
        "catia_surface_loft lofts through the sections and catia_close_surface fills "
        "the result into a solid"
    ),
    "catia_pad_drafted_filleted": _not_needed(
        "Pad with catia_pad, then catia_draft the sides and catia_fillet_edges the edges"
    ),
    "catia_pattern_explode": _not_needed(),
    "catia_replace_face": _not_needed(),
    "catia_update": (
        "The open kernel rebuilds on every call, so its part is never out of date; "
        "measure it directly"
    ),
    # -- Sketcher ----------------------------------------------------------------
    "catia_sketch_analysis": _not_needed(),
    "catia_sketch_chamfer": _not_needed(
        "Draw the chamfered outline directly with catia_sketch_polyline"
    ),
    "catia_sketch_conic": _not_needed(
        "catia_sketch_ellipse draws an ellipse, and catia_sketch_spline a free curve"
    ),
    "catia_sketch_corner": _not_needed(
        "Round the solid's edge afterwards with catia_fillet_edges"
    ),
    "catia_sketch_dimension": _not_needed(
        "The profile tools take their sizes as arguments, so draw at the size you want; "
        "the coordinates are the dimension"
    ),
    "catia_sketch_gear_profile": _not_needed(),
    "catia_sketch_groove_profile": _not_needed(),
    "catia_sketch_intersect_3d": _not_needed(
        "catia_curve_section cuts the part with a plane as a 3D curve"
    ),
    "catia_sketch_mirror": _not_needed(
        "Mirror the solid instead with catia_mirror or catia_symmetry"
    ),
    "catia_sketch_offset": _not_needed(),
    "catia_sketch_parallelogram": _not_needed(
        "Draw the four corners with catia_sketch_polyline"
    ),
    "catia_sketch_pattern": _not_needed(
        "Pattern the solid feature instead with catia_pattern_rectangular or "
        "catia_pattern_circular"
    ),
    "catia_sketch_project": _not_needed(),
    "catia_sketch_revolve_profile": _not_needed(
        "Draw the profile with catia_sketch_rectangle and the axis with "
        "catia_sketch_axis, then catia_shaft"
    ),
    "catia_sketch_rotate": _not_needed(),
    "catia_sketch_scale": _not_needed(),
    "catia_sketch_translate": _not_needed(),
    "catia_sketch_trim": _not_needed(),
    # -- Generative Shape Design ---------------------------------------------------
    "catia_surface_blend": _not_needed(),
    "catia_surface_sweep": _not_needed(
        "catia_rib sweeps a closed profile along a path as a solid"
    ),
}


def reason_for(tool: str) -> str:
    """Why `tool` is not implemented, or an empty string for one with no entry.

    Empty rather than raising, because the runner asks this about any name it has no
    handler for, including one the registry does not declare. The partition test
    is what makes the empty case impossible for a declared operation.
    """
    return REASONS.get(tool, "")


__all__ = ["REASONS", "reason_for"]
