# 968 symbols added — Kryova backend, 2026-09-01 → 09-03

## app/ai/resume.py (8)
_age, _clean, _history_entry, _subject, build_history, catia_activity, empty, resume_lines

## app/ai/state.py (2)
_catia_reference_lines, _catia_ui_language

## app/ai/tools.py (2)
_design_history, design_history

## app/catia/connection.py (2)
_language_code, offers

## app/catia/dispatch.py (5)
_document_scope, _resolve_ui, _uploaded_file, connected_ui_language, offered_tool_specs

## app/catia/ops/assembly.py (16)
catia_assembly_analysis, catia_assembly_clash, catia_assembly_feature, catia_bill_of_materials, catia_component_add, catia_component_fix, catia_component_move, catia_component_multi_instantiate, catia_component_properties, catia_component_remove, catia_component_replace, catia_constrain, catia_constraint_set_active, catia_constraint_update, catia_product_create, catia_scene_explode

## app/catia/ops/drafting.py (15)
catia_annotation_add, catia_datum_add, catia_dimension_add, catia_dimension_chain, catia_dimension_generate, catia_drawing_create, catia_drawing_update, catia_dressup_add, catia_sheet_add, catia_sheet_frame, catia_table_add, catia_tolerance_add, catia_view_add, catia_view_align, catia_view_properties

## app/catia/ops/infrastructure.py (10)
catia_capture_view, catia_checkpoint, catia_export, catia_export_step, catia_import, catia_new_part, catia_open_document, catia_restore, catia_set_material, catia_status

## app/catia/ops/inspection.py (8)
catia_analysis_part, catia_delete_feature, catia_list_features, catia_measure, catia_measure_between, catia_measure_item, catia_select, catia_update

## app/catia/ops/knowledge.py (11)
catia_check_create, catia_design_table_activate, catia_design_table_create, catia_formula_create, catia_knowledge_report, catia_list_parameters, catia_measure_publish, catia_parameter_create, catia_parameter_set_create, catia_rule_create, catia_set_parameter

## app/catia/ops/part_design.py (45)
_limit_params, catia_affinity, catia_body_activate, catia_body_create, catia_boolean, catia_chamfer, catia_draft, catia_feature_activate, catia_feature_parents, catia_feature_rename, catia_feature_reorder, catia_fillet, catia_fillet_edges, catia_fillet_face, catia_fillet_tritangent, catia_fillet_variable, catia_geometrical_set, catia_groove, catia_hole, catia_hole_at, catia_hole_pattern, catia_mirror, catia_multi_section_solid, catia_pad, catia_pad_drafted_filleted, catia_pattern_circular, catia_pattern_explode, catia_pattern_rectangular, catia_pattern_user, catia_pocket, catia_remove_face, catia_replace_face, catia_rib, catia_rotate, catia_scale, catia_shaft, catia_shell, catia_shell_faces, catia_slot, catia_solid_combine, catia_stiffener, catia_symmetry, catia_thickness, catia_thread, catia_translate

## app/catia/ops/reference.py (19)
catia_axis_system, catia_line_between, catia_line_direction, catia_line_normal, catia_line_tangent, catia_list_edges, catia_list_faces, catia_plane_angle, catia_plane_mean, catia_plane_normal_to_curve, catia_plane_offset, catia_plane_tangent_to_surface, catia_plane_through_points, catia_planes_between, catia_point_at, catia_point_between, catia_point_centre, catia_point_on_curve, catia_point_on_surface

## app/catia/ops/registry.py (8)
_build, by_tier, by_workbench, get, long_running_names, mutating_names, no_auto_checkpoint_names, summary

## app/catia/ops/sketcher.py (34)
_sketch_target, catia_sketch_analysis, catia_sketch_arc, catia_sketch_arc_three_point, catia_sketch_axis, catia_sketch_chamfer, catia_sketch_circle, catia_sketch_close, catia_sketch_conic, catia_sketch_constrain, catia_sketch_corner, catia_sketch_create, catia_sketch_dimension, catia_sketch_ellipse, catia_sketch_gear_profile, catia_sketch_groove_profile, catia_sketch_intersect_3d, catia_sketch_line, catia_sketch_mirror, catia_sketch_offset, catia_sketch_parallelogram, catia_sketch_pattern, catia_sketch_point, catia_sketch_polygon, catia_sketch_polyline, catia_sketch_project, catia_sketch_rectangle, catia_sketch_revolve_profile, catia_sketch_rotate, catia_sketch_scale, catia_sketch_slot, catia_sketch_spline, catia_sketch_translate, catia_sketch_trim

## app/catia/ops/spec.py (33)
__post_init__, _schema_over, angle, bounded_number, coordinate, count, daemon_schema, direction3, distance, feature_length, flag, for_server, from_server, json_schema, length, mutating, name_list, name_of, name_pair, new_name, one_of, optional, point2, point3, point_list, ratio, raw, required, server_supplied_fields, signed_angle, text, thickness, tilt

## app/catia/ops/surfaces.py (21)
catia_boundary, catia_close_surface, catia_disassemble, catia_extract, catia_extrapolate, catia_healing, catia_join, catia_sew_surface, catia_split, catia_surface_analysis, catia_surface_blend, catia_surface_extrude, catia_surface_fill, catia_surface_loft, catia_surface_offset, catia_surface_primitive, catia_surface_revolve, catia_surface_sweep, catia_thick_surface, catia_trim, catia_untrim

## app/catia/ops/ui.py (9)
catia_describe_dialog, catia_dialog_action, catia_fill_dialog, catia_graphic_properties, catia_list_commands, catia_press_key, catia_run_command, catia_switch_workbench, catia_view_control

## app/catia/ops/vocabulary.py (5)
edge_reference, element_reference, face_reference, origin_plane, support

## app/catia/ops/wireframe.py (15)
catia_curve_circle, catia_curve_combine, catia_curve_connect, catia_curve_corner, catia_curve_extremum, catia_curve_helix, catia_curve_intersect, catia_curve_offset_3d, catia_curve_parallel, catia_curve_polyline, catia_curve_project, catia_curve_reflect_line, catia_curve_section, catia_curve_spiral, catia_curve_spline

## app/catia_kb/ui.py (9)
_command_entries, _folded_button_index, button_labels, forbidden_reason, menu_title, resolve_command, resolve_workbench, role_of, translated

## app/solve/loads.py (11)
_bearing, _body_force, _centrifugal, _dofs, _element_volumes, _facet_areas_and_normals, _force, _gravity, _moment, _pressure, assemble_loads

## app/solve/selection.py (4)
_axis_frame, _select_cylinder, _select_sphere, radial_offsets

## app/solve/types.py (3)
_implied_dofs, _resolve_dofs, _tagged_force

## scripts/catia_bridge/__main__.py (2)
_open_backend, _wait_for_backend

## scripts/catia_bridge/backend.py (12)
_core_methods, describe_dialog, dialog_action, ensure_document, fill_dialog, implemented_tools, list_commands, press_key, run_command, select, switch_workbench, unsupported

## scripts/catia_bridge/catia_com.py (20)
_after_command, _detect_ui_language, _find_named, _free_document_path, _is_active, _main_window, _match_control, _resolve_document_path, _same_document, _selection_count, _workbench_name, describe_dialog, dialog_action, ensure_document, fill_dialog, list_commands, press_key, run_command, select, switch_workbench

## scripts/catia_bridge/com/_context.py (15)
_body, _bounding_box, _discard, _document, _feature_list, _feature_result, _part, _solid_volume, append_and_name, direction_of, geometrical_set, named_face_plane, reference_to, resolve_element, resolve_support

## scripts/catia_bridge/com/assembly.py (25)
_add_existing, _assembly_reference, _children, _component, _component_names, _find_component, _is_constrained, _place, _position, _product, _rotate, _set_orientation, _unit, component_add, component_fix, component_move, component_multi_instantiate, component_properties, component_remove, component_replace, constrain, constraint_set_active, constraint_update, product_create, scene_explode

## scripts/catia_bridge/com/assembly_review.py (15)
_broken_links, _configure_feature, _constraint_report, _dependencies, _freedom_report, _is_fixed, _mass_report, _number, _text, _walk, assembly_analysis, assembly_clash, assembly_feature, bill_of_materials, visit

## scripts/catia_bridge/com/drafting.py (30)
_add_derived, _add_front, _apply_format, _dimension_value, _drawing, _fill_bom, _find_document, _front_source, _hatch, _set_text_around, _set_tolerance, _sheet, _sheet_point, _view, _view_element, annotation_add, datum_add, dimension_add, dimension_chain, dimension_generate, drawing_create, drawing_update, dressup_add, sheet_add, sheet_frame, table_add, tolerance_add, view_add, view_align, view_properties

## scripts/catia_bridge/com/infrastructure.py (16)
_aim, _apply_export_settings, _describe_import, _format_from_suffix, _heal, _render, _safe_name, _safe_stem, _scale_document, _set_visibility, _verify_transfer, _visibility, export, graphic_properties, import_file, view_control

## scripts/catia_bridge/com/inspection.py (4)
_measurable, analysis_part, measure_between, measure_item

## scripts/catia_bridge/com/knowledge.py (10)
_relation_kind, check_create, design_table_activate, design_table_create, formula_create, knowledge_report, measure_publish, parameter_create, parameter_set_create, rule_create

## scripts/catia_bridge/com/part_design.py (35)
_swept_feature, _transform, affinity, body_activate, body_create, boolean, draft, feature_activate, feature_parents, feature_rename, feature_reorder, fillet_edges, fillet_face, fillet_tritangent, fillet_variable, geometrical_set, hole_at, hole_pattern, multi_section_solid, pad_drafted_filleted, pattern_explode, pattern_user, remove_face, replace_face, rib, rotate, scale, shell_faces, slot, solid_combine, stiffener, symmetry, thickness, thread, translate

## scripts/catia_bridge/com/reference.py (24)
_curve_kind, _edge_references, _face_reference, _search_topology, _surface_kind, axis_system, line_between, line_direction, line_normal, line_tangent, list_edges, list_faces, plane_angle, plane_mean, plane_normal_to_curve, plane_offset, plane_tangent_to_surface, plane_through_points, planes_between, point_at, point_between, point_centre, point_on_curve, point_on_surface

## scripts/catia_bridge/com/sketch_edit.py (20)
__enter__, __exit__, _create_2d, _element_2d, _mark_collection, _pair, _read_2d, _transform, _write_2d, sketch_chamfer, sketch_corner, sketch_intersect_3d, sketch_mirror, sketch_offset, sketch_pattern, sketch_project, sketch_rotate, sketch_scale, sketch_translate, sketch_trim

## scripts/catia_bridge/com/sketcher.py (28)
_add_constraint, _between_ccw, _draw, _mark, _open_sketch, _require_closed, _rotate_about, _sketch_reference, draw, sketch_analysis, sketch_arc, sketch_arc_three_point, sketch_axis, sketch_circle, sketch_close, sketch_conic, sketch_constrain, sketch_create, sketch_dimension, sketch_ellipse, sketch_line, sketch_parallelogram, sketch_point, sketch_polygon, sketch_polyline, sketch_rectangle, sketch_slot, sketch_spline

## scripts/catia_bridge/com/surfaces.py (22)
_build, boundary, close_surface, disassemble, extract, extrapolate, healing, join, sew_surface, split, surface_analysis, surface_blend, surface_extrude, surface_fill, surface_loft, surface_offset, surface_primitive, surface_revolve, surface_sweep, thick_surface, trim, untrim

## scripts/catia_bridge/com/wireframe.py (16)
_curve, curve_circle, curve_combine, curve_connect, curve_corner, curve_extremum, curve_helix, curve_intersect, curve_offset_3d, curve_parallel, curve_polyline, curve_project, curve_reflect_line, curve_section, curve_spiral, curve_spline

## scripts/catia_bridge/expressions.py (5)
_strip_units, _walk, evaluate, parameter_names, replace

## scripts/catia_bridge/mock/knowledge.py (13)
_apply_row, _evaluate_check, _knowledge_state, _solve_formulas, check_create, design_table_activate, design_table_create, formula_create, knowledge_report, measure_publish, parameter_create, parameter_set_create, rule_create

## scripts/catia_bridge/mock_catia.py (17)
__init__, _commit_chamfer, _commit_fillet, _commit_pad, _commit_pocket, _dialog_sketch, _dimension, _wire_ui, describe_dialog, dialog_action, ensure_document, fill_dialog, list_commands, press_key, run_command, select, switch_workbench

## scripts/catia_bridge/mock_ui.py (17)
__init__, _act, _build_dialog, _enabled, _english, _field, _flatten, _menu_node, active_dialog, dialog_open, fill, invoke_menu, press, press_key, read_menu, say, start_command

## scripts/catia_bridge/session.py (1)
_ensure_document

## scripts/catia_bridge/sketch_geometry.py (19)
_corner_frame, _retrim, _unit_away_from, apply, chamfer, circular_pattern, closed, corner, direction, length, line_intersection, offset, rectangular_pattern, reflection, rotation, scale, scaling, translation, trim

## scripts/catia_bridge/tool_table.py (1)
_enum_of

## scripts/catia_bridge/ui_automation.py (38)
_button_kind, _child_id, _class_name, _classify, _combo_options, _control_text, _enum_children, _enum_top_level, _fold_label, _label_fields, _msaa_menu_labels, _post, _process_of, _read_controls, _read_menu_level, _rect, _send, _user32, _window_text, active_dialog, buttons, callback, click, describe, detect_language, fields, find_menu_item, invoke_menu, is_submenu, main_window, press_key, read_dialog, read_menu, set_checked, set_choice, set_text, walk, window_titled

## scripts/catia_bridge/ui_policy.py (3)
check, fold, refusal

## scripts/gen_bridge_tools.py (4)
_check_keywords, _formatted, main, render

## tests/test_ai_context.py (1)
offered_tool_specs

## tests/test_bridge_table_is_generated.py (5)
_bridge_table, test_every_model_facing_field_is_accepted_by_the_daemon, test_the_checked_in_table_is_what_the_generator_produces, test_the_daemon_knows_every_tool_the_server_can_send, test_tiers_agree_between_the_two_sides

## tests/test_catia_com_contract.py (10)
_dialog, test_a_button_is_not_offered_as_a_field, test_a_known_workbench_goes_through_start_workbench, test_a_name_that_is_not_in_the_part_names_the_tool_that_lists_them, test_a_trailing_colon_in_the_dialog_is_tolerated, test_an_empty_selection_clears_rather_than_failing, test_an_exact_label_matches, test_an_unknown_field_lists_the_real_ones, test_case_and_accents_do_not_matter, test_selecting_by_name_adds_the_element_catia_found

## tests/test_catia_dispatch.py (4)
test_a_conversation_with_no_document_scopes_nothing, test_every_modelling_call_names_the_document_it_is_for, test_the_auto_checkpoint_is_scoped_to_the_part_it_is_protecting, test_the_tools_that_establish_the_binding_are_not_scoped_by_it

## tests/test_catia_e2e.py (6)
test_every_interactive_call_is_written_to_the_operation_log, test_running_a_command_is_checkpointed_first, test_selecting_a_feature_crosses_the_wire, test_the_daemon_refuses_a_command_no_checkpoint_could_undo, test_the_interactive_loop_builds_a_part_end_to_end, test_the_live_menu_is_readable_and_reports_availability

## tests/test_catia_import_resolution.py (10)
_conversation, _upload, project, test_a_conversation_with_no_project_has_nothing_to_import, test_a_named_upload_travels_as_bytes, test_an_unknown_name_lists_what_the_project_does_have, test_another_project_s_upload_is_not_reachable, test_no_conversation_at_all_is_refused_rather_than_searched, test_the_name_may_be_given_without_its_extension, user

## tests/test_catia_interactive.py (67)
build_profile, call, make_session, press, run_command, test_a_button_label_maps_to_its_role_in_every_language, test_a_checkbox_takes_a_word_not_a_number, test_a_command_a_dialog_a_field_and_ok_builds_the_feature, test_a_command_resolves_to_the_seats_label_first, test_a_dialog_separates_its_fields_from_its_buttons, test_a_dropdown_only_accepts_its_own_options, test_a_field_takes_the_label_to_its_left, test_a_field_with_no_label_nearby_keeps_none, test_a_forbidden_candidate_anywhere_in_the_list_refuses_the_call, test_a_greyed_command_is_listed_as_unavailable_not_omitted, test_a_hello_frame_without_a_language_is_not_a_failure, test_a_menu_bar_identifies_its_language, test_a_menu_item_walks_its_own_subtree, test_a_named_button_is_passed_through_untranslated, test_a_reported_language_is_normalised_not_trusted, test_a_same_row_label_beats_one_on_the_line_above, test_a_trailing_colon_is_not_part_of_the_name, test_an_ambiguous_command_reports_the_alternatives, test_an_empty_selection_clears_it, test_an_ordinary_tool_still_fails_fast_on_a_wedged_catia, test_an_unknown_button_label_has_no_role, test_an_unknown_command_is_ignored_silently_as_catia_ignores_it, test_an_unknown_command_is_passed_through_rather_than_refused, test_an_unknown_command_names_the_discovery_tool, test_an_unknown_language_falls_back_to_english_rather_than_failing, test_an_unrecognised_menu_bar_reports_no_language_rather_than_guessing, test_an_untranslated_command_says_so_instead_of_inventing_one, test_both_sides_of_the_wire_agree_on_what_is_forbidden, test_cancel_changes_nothing, test_describe_reports_no_dialog_rather_than_failing, test_enter_confirms_it, test_escape_abandons_the_dialog_like_a_keyboard_would, test_every_interactive_tool_is_on_both_sides_of_the_wire, test_every_out_of_band_tool_exists_and_is_read_or_dialog_work, test_every_role_still_offers_english_when_the_language_is_unknown, test_filling_a_field_that_does_not_exist_lists_the_ones_that_do, test_filling_with_no_dialog_open_says_to_run_the_command_first, test_it_does_not_refuse_things_that_merely_contain_a_forbidden_word, test_it_imports_on_linux_and_refuses_rather_than_crashing, test_it_refuses_them_in_the_other_languages_too, test_it_resolves_a_button_role_into_seat_labels, test_it_speaks_the_language_it_was_built_with, test_no_language_still_resolves_to_english, test_one_coincidental_match_is_not_a_detection, test_preview_does_not_commit, test_running_a_greyed_command_explains_why_rather_than_silently_failing, test_search_narrows_it, test_selecting_a_sketch_is_what_the_dialog_pads, test_selecting_something_that_is_not_there_says_so, test_switching_workbench_reports_the_licence_it_needs, test_the_agent_is_taught_the_loop_rather_than_left_to_infer_it, test_the_daemon_refuses_what_no_checkpoint_could_undo, test_the_daemon_reports_which_language_it_is_running_in, test_the_dialog_tools_bypass_the_com_liveness_probe, test_the_dispatcher_resolves_a_command_into_seat_labels, test_the_interactive_tools_are_not_auto_checkpointed, test_the_key_list_is_closed, test_the_menu_is_reported_in_the_seats_own_words, test_the_seats_language_is_tried_before_english, test_the_two_tables_do_not_overlap, test_two_commands_at_once_is_refused_rather_than_stacked, wedged

## tests/test_catia_kb.py (3)
test_a_blocked_word_stays_reachable_as_a_phrase, test_grammar_in_an_interface_language_is_not_a_command, test_the_guard_is_case_sensitive_so_codes_survive

## tests/test_com_backend_covers_the_registry.py (5)
_public_methods, test_core_methods_are_implemented_by_both_backends, test_every_operation_has_a_com_implementation, test_no_com_method_is_left_behind_after_a_rename, test_the_mock_reports_the_tools_it_actually_has

## tests/test_document_binding.py (14)
call, make_part, mock, session, shape, test_a_call_with_no_document_behaves_as_it_always_did, test_a_document_that_left_the_workstation_is_refused_by_name, test_a_malformed_envelope_is_ignored_rather_than_obeyed, test_a_reopened_part_still_weighs_what_it_weighed, test_a_scoped_call_runs_against_the_document_it_names, test_both_take_the_same_arguments, test_neither_backend_inherits_the_no_op, test_the_document_already_in_hand_is_not_reloaded, test_the_interactive_tools_are_never_scoped

## tests/test_expressions.py (22)
test_a_formula_with_no_parameters_reads_nothing, test_a_huge_exponent_cannot_be_used_to_hang_the_bridge, test_a_satisfied_condition_is_true, test_a_string_constant_is_not_a_dimension, test_a_syntax_error_says_so_rather_than_crashing, test_a_unit_with_a_space_before_it_is_stripped_too, test_a_violated_condition_is_false, test_an_unknown_function_is_refused_by_name, test_an_unknown_parameter_is_named_along_with_what_does_exist, test_anything_that_is_not_arithmetic_is_refused, test_attribute_access_is_refused_even_on_a_real_parameter, test_conditions_combine, test_degrees_are_recognised_as_a_unit, test_division_by_zero_is_reported_not_raised_as_arithmetic, test_does_not_report_functions_as_parameters, test_evaluates_arithmetic_over_parameters, test_inline_millimetres_are_stripped, test_operator_precedence_is_pythons_not_left_to_right, test_reports_each_name_once, test_reports_what_a_formula_reads, test_the_maths_functions_a_dimension_actually_uses, test_trigonometry_is_in_degrees_like_the_rest_of_the_system

## tests/test_mock_knowledge.py (21)
_length, catia, test_a_chain_of_formulas_settles, test_a_check_follows_the_parameter_it_watches, test_a_circular_pair_is_refused_and_not_kept, test_a_configuration_drives_the_formulas_too, test_a_created_parameter_is_typed_and_listed, test_a_duplicate_name_is_refused, test_a_formula_computes_its_parameter_immediately, test_a_formula_naming_a_parameter_that_does_not_exist_is_refused, test_a_formula_that_reads_itself_is_refused, test_a_name_no_formula_could_reference_is_refused, test_a_ragged_row_is_refused_with_its_number, test_a_row_outside_the_table_is_refused, test_a_rule_is_recorded_and_says_it_did_not_run, test_a_satisfied_check_reports_satisfied, test_a_value_outside_its_own_bounds_is_refused, test_activating_a_row_writes_its_configuration, test_an_angle_carries_degrees_not_millimetres, test_changing_an_input_recomputes_the_formula, test_formulas_survive_a_checkpoint_and_restore

## tests/test_resume.py (20)
conversation, log, test_a_clean_conversation_gets_no_unfinished_line, test_a_conversation_that_never_touched_catia_costs_nothing, test_a_conversation_with_no_binding_says_so_rather_than_erroring, test_a_failure_that_was_later_made_to_work_is_not_a_loose_end, test_a_flood_of_loose_ends_is_counted_rather_than_listed, test_a_limit_beyond_the_page_size_is_clamped_not_obeyed, test_a_long_session_pages_and_says_how_much_it_left, test_an_operation_says_what_it_acted_on_and_what_it_produced, test_an_unfinished_operation_is_named_with_what_it_said, test_catia_text_cannot_close_the_state_block_it_lands_in, test_failures_only_narrows_to_the_loose_ends, test_one_conversations_history_is_not_anothers, test_operations_come_back_in_build_order, test_repeated_failures_are_counted_rather_than_repeated, test_the_block_says_how_much_was_done_and_how_long_ago, test_the_last_attempt_is_what_counts_not_the_first, test_the_newest_operations_are_the_ones_kept, test_the_scan_is_bounded_but_the_count_is_not

## tests/test_retrieval.py (18)
test_a_cut_instruction_is_not_a_heading, test_a_dimension_with_a_thread_designation_keeps_the_thread, test_a_passage_with_no_heading_still_renders_a_citation, test_a_path_fragment_is_not_a_heading, test_a_single_passage_is_returned_unchanged, test_an_empty_list_is_returned_unchanged, test_boosts_scale_scores_relative_to_unweighted, test_empty_boosts_list_does_not_raise, test_every_spelling_of_the_diameter_sign_is_one_term, test_never_stem_entries_are_returned_unchanged, test_no_analyzed_term_exceeds_max_length, test_search_with_limit_zero_returns_empty, test_single_digit_diameter_oe_is_preserved, test_stemmer_floor_keeps_short_technical_words_intact, test_tet4_is_kept_whole_and_also_split, test_the_diameter_sign_survives_however_it_was_typed, test_the_rejections_do_not_take_real_headings_with_them, test_zero_limit_returns_empty_without_error

## tests/test_retrieval_corpus.py (4)
setup_corpus, test_knowledge_service_language_preference, test_multi_term_coverage_floor, test_top_results_relevance

## tests/test_retrieval_tuning.py (2)
calculate_mrr, test_default_parameters_are_optimal_or_near_optimal

## tests/test_sketch_geometry.py (47)
distance, test_a_chamfer_longer_than_its_element_is_refused, test_a_circle_changes_radius_and_keeps_its_centre, test_a_full_circle_divides_by_the_count, test_a_grid_fills_both_directions, test_a_line_moves_along_its_own_normal, test_a_mirrored_arc_is_the_arc_asked_for_not_its_complement, test_a_mirrored_full_circle_stays_a_full_circle, test_a_negative_radius_is_refused, test_a_partial_arc_puts_an_instance_at_each_end, test_a_radius_that_does_not_fit_says_so_with_both_numbers, test_a_sharp_corner_takes_a_longer_bite_than_a_right_angle, test_a_zero_length_mirror_axis_is_refused, test_a_zero_scale_factor_is_refused, test_all_but_parallel_elements_are_caught_by_the_second_guard, test_an_angle_that_cannot_close_the_triangle_is_refused, test_an_unknown_mode_is_refused, test_collinear_elements_have_no_corner, test_crossing_lines_meet_where_they_cross, test_elements_that_stop_short_are_extended_to_meet, test_equal_lengths_at_forty_five_degrees, test_instances_orbit_the_given_centre, test_instances_step_by_the_spacing, test_keeping_the_first_leaves_the_second_alone, test_offsetting_a_circle_inwards_past_its_centre_is_refused, test_one_instance_is_not_a_pattern, test_overlong_elements_are_cut_back_to_the_crossing, test_parallel_lines_are_refused_by_name, test_reflection_about_a_slanted_axis, test_reflection_about_the_u_axis_flips_v, test_reversing_offsets_the_other_way, test_rotation_carries_an_arc_sweep_with_it, test_rotation_turns_about_the_given_centre, test_scaling_grows_an_arc_radius, test_scaling_holds_the_centre_still, test_segments_that_stop_short_still_meet, test_the_angle_form_produces_that_angle, test_the_arc_is_tangent_to_both_elements, test_the_arc_sweeps_the_short_way_round, test_the_elements_are_trimmed_to_the_tangent_points, test_the_longer_portion_is_the_one_that_survives, test_the_original_is_not_repeated_on_top_of_itself, test_the_second_spacing_defaults_to_the_first, test_the_surviving_arm_keeps_its_far_endpoint, test_translation_moves_a_segment, test_two_explicit_lengths_are_used_as_given, test_zero_spacing_is_refused

## tests/test_solver_loads.py (34)
_clamp, _resultant, bar, base, bore, peak, test_a_body_on_its_own_axis_is_balanced, test_a_clamp_holds_everything, test_a_dofs_that_agrees_with_the_kind_is_accepted, test_a_dofs_that_contradicts_the_kind_is_refused, test_a_higher_exponent_concentrates_the_load, test_a_pressure_on_interior_nodes_is_refused, test_a_region_on_the_axis_has_no_lever_arm, test_a_roller_holds_only_its_normal, test_a_roller_really_does_let_the_face_slide, test_a_roller_without_a_normal_is_refused, test_a_slider_holds_the_other_two, test_a_stored_load_case_without_a_type_still_solves, test_a_validated_fixture_survives_being_validated_again, test_a_zero_direction_is_refused, test_a_zero_moment_is_refused, test_axial_stress_matches_the_equivalent_force, test_denser_material_weighs_more, test_force_points_away_from_the_axis, test_it_applies_the_requested_moment_and_no_net_force, test_it_is_quadratic_in_nothing_but_linear_in_g, test_it_refuses_a_non_cylindrical_region, test_it_scales_with_area_where_a_force_would_not, test_load_is_quadratic_in_speed, test_negative_pressure_pulls, test_only_the_loaded_half_carries_the_load, test_resultant_is_mass_times_g, test_resultant_is_pressure_times_area, test_symmetry_is_a_roller_by_another_name
