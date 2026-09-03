"""Compiling a design into the operations that build it.

Everything Layer B promises is checked here, and each test is named after the
failure it prevents rather than after the function it calls.

* **The plan is deterministic.** Same spec, same calls, same digest. Without
  that, roadmap I5 is a slogan and a cached simulation result is a guess.
* **A mistake is caught at the spec, not at the seat.** An operation that does
  not exist, an argument it does not take, a value out of bounds, a reference
  that points forwards, a formula that computes degrees where millimetres were
  wanted. Today every one of those arrives as a COM error thirty operations into
  a rebuild.
* **Everything the design refers to is named by the design** (B2), including the
  Part Design features that CATIA will not let you name on creation.

Offline: no database fixture, no CATIA, no gmsh.
"""

import pytest

from app.catia.ops import registry
from app.catia.ops.spec import Tier
from app.design.compile import (
    CREATES_TREE_FEATURE,
    NOT_REPRODUCIBLE,
    Created,
    bind,
    compile_spec,
)
from app.design.errors import (
    DesignReferenceError,
    FeatureError,
    PolicyError,
    UnitError,
)
from app.design.params import Parameter, Unit
from app.design.spec import DesignSpec, FeatureSpec, expr, ref


def bracket(**overrides: object) -> DesignSpec:
    """A small but real design: sketch, rectangle, pad, fillet on the pad."""
    return DesignSpec.of(
        overrides.get("name", "Bracket"),  # type: ignore[arg-type]
        material="aluminium-6061-t6",
        parameters=[
            Parameter("width_mm", Unit.MM, value=120.0),
            Parameter("depth_mm", Unit.MM, value=80.0),
            Parameter("thick_mm", Unit.MM, value=8.0),
            Parameter("fillet_mm", Unit.MM, expression="thick_mm / 2"),
            Parameter("lighten", Unit.NONE, expression="thick_mm >= 12"),
        ],
        features=[
            FeatureSpec("plate.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "plate.outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("plate.profile"),
                    "width_mm": expr("width_mm"),
                    "height_mm": expr("depth_mm"),
                },
            ),
            FeatureSpec(
                "plate.body",
                "catia_pad",
                {"sketch": ref("plate.profile"), "length_mm": expr("thick_mm")},
                note="Extrude the footprint to thickness.",
            ),
            FeatureSpec(
                "plate.edges",
                "catia_fillet",
                {
                    "feature": ref("plate.body"),
                    "radius_mm": expr("fillet_mm"),
                    "edges": "vertical",
                },
            ),
            FeatureSpec(
                "plate.window", "catia_sketch_create", {"support": "XY"}, when="lighten"
            ),
        ],
    )


class TestThePreamble:
    def test_a_plan_starts_by_creating_the_document(self) -> None:
        """A plan builds the part from nothing; that is what makes it replayable."""
        plan = compile_spec(bracket())
        assert plan.calls[0].tool == "catia_new_part"
        assert plan.calls[0].arguments == {"name": "Bracket"}

    def test_the_material_is_set_before_anything_is_weighed(self) -> None:
        plan = compile_spec(bracket())
        assert plan.calls[1].tool == "catia_set_material"
        assert plan.calls[1].arguments == {"material": "aluminium-6061-t6"}

    def test_the_server_supplied_density_is_not_sent(self) -> None:
        """`catia_set_material.density_kg_m3` is filled by the server.

        A plan that carried it would be refused by the model-facing schema's
        `additionalProperties: false` — the exact asymmetry the operation spec
        warns about.
        """
        plan = compile_spec(bracket())
        assert "density_kg_m3" not in plan.calls[1].arguments

    def test_a_design_with_no_material_skips_that_call(self) -> None:
        design = DesignSpec.of("D", features=[FeatureSpec("s", "catia_sketch_create", {"support": "XY"})])
        assert compile_spec(design).tools() == ("catia_new_part", "catia_sketch_create")

    def test_create_document_false_compiles_the_features_alone(self) -> None:
        plan = compile_spec(bracket(), create_document=False)
        assert "catia_new_part" not in plan.tools()
        assert plan.calls[0].tool == "catia_sketch_create"


class TestNamingIsTheDesigns:
    """B2: every element the design refers to carries the design's own name."""

    def test_an_operation_that_takes_a_name_gets_the_semantic_one(self) -> None:
        plan = compile_spec(bracket())
        sketch = plan.calls_for("plate.profile")[0]
        assert sketch.arguments["name"] == "plate_profile"

    def test_an_explicit_name_in_the_spec_is_not_overwritten(self) -> None:
        design = DesignSpec.of(
            "D",
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY", "name": "Chosen"})
            ],
        )
        assert compile_spec(design).calls[-1].arguments["name"] == "Chosen"

    def test_a_pad_is_renamed_straight_after_it_is_built(self) -> None:
        """CATIA will not let a pad be named on creation, so B2 needs two calls.

        Done with `catia_feature_rename`, which already ships and is already
        tested, rather than by adding a `name` parameter to fifty backend
        methods that nobody can validate against a real seat today.
        """
        plan = compile_spec(bracket())
        pad, rename = plan.calls_for("plate.body")
        assert pad.tool == "catia_pad"
        assert rename.tool == "catia_feature_rename"
        assert rename.arguments["name"] == "plate_body"

    def test_the_rename_names_the_feature_by_what_the_creating_call_reported(self) -> None:
        """Predicting `Pad.1` is the positional fragility this package removes."""
        plan = compile_spec(bracket())
        rename = plan.calls_for("plate.body")[1]
        assert rename.arguments["feature"] == Created("plate.body")

    def test_a_later_reference_resolves_to_a_literal_because_of_that_rename(self) -> None:
        """The whole payoff: the fillet names the pad, not a position in a tree."""
        plan = compile_spec(bracket())
        fillet = plan.calls_for("plate.edges")[0]
        assert fillet.arguments["feature"] == "plate_body"

    def test_a_sketch_internal_operation_is_not_renamed(self) -> None:
        """A rectangle drawn inside a sketch leaves no tree row to rename.

        Emitting a rename for it would fail at the workstation — which is the
        failure this compiler exists to move earlier, not to cause.
        """
        plan = compile_spec(bracket())
        assert [c.tool for c in plan.calls_for("plate.outline")] == ["catia_sketch_rectangle"]

    def test_such_a_feature_is_reported_as_unaddressable(self) -> None:
        plan = compile_spec(bracket())
        assert plan.unaddressable == {"plate.outline": "catia_sketch_rectangle"}

    def test_referring_to_an_unaddressable_feature_says_why(self) -> None:
        design = DesignSpec.of(
            "D",
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "s.rect",
                    "catia_sketch_rectangle",
                    {"sketch": ref("s.a"), "width_mm": 10, "height_mm": 10},
                ),
                FeatureSpec("p.body", "catia_pad", {"sketch": ref("s.rect"), "length_mm": 5}),
            ],
        )
        with pytest.raises(DesignReferenceError, match="no handle on what it made"):
            compile_spec(design)


class TestTheTreeFeatureTableStaysTrue:
    """`CREATES_TREE_FEATURE` is curated, so it needs a guard against drift.

    Each entry is a claim about an upstream operation. If one is renamed,
    retired, or gains a `name` parameter of its own, the claim goes stale — and
    a stale claim here means either a rename that fails at a seat or a feature
    that quietly stops being addressable.
    """

    def test_every_entry_still_exists(self) -> None:
        missing = sorted(name for name in CREATES_TREE_FEATURE if registry.get(name) is None)
        assert not missing, f"no longer in the registry: {missing}"

    def test_every_entry_is_still_a_write(self) -> None:
        for name in sorted(CREATES_TREE_FEATURE):
            operation = registry.get(name)
            assert operation is not None
            assert operation.tier is Tier.WRITE, name

    def test_no_entry_has_gained_a_name_parameter(self) -> None:
        """If one has, it belongs in the other branch and the rename is now noise."""
        gained = sorted(
            name
            for name in CREATES_TREE_FEATURE
            if any(p.name == "name" for p in registry.OPERATIONS_BY_NAME[name].params)
        )
        assert not gained, f"these now take a name and should leave the table: {gained}"

    def test_the_refusal_table_names_real_operations(self) -> None:
        missing = sorted(name for name in NOT_REPRODUCIBLE if registry.get(name) is None)
        assert not missing, f"refusing operations that no longer exist: {missing}"


class TestExpressionsAndReferences:
    def test_expressions_become_numbers(self) -> None:
        plan = compile_spec(bracket())
        assert plan.calls_for("plate.body")[0].arguments["length_mm"] == pytest.approx(8.0)
        assert plan.calls_for("plate.edges")[0].arguments["radius_mm"] == pytest.approx(4.0)

    def test_a_reference_becomes_the_catia_name(self) -> None:
        plan = compile_spec(bracket())
        assert plan.calls_for("plate.outline")[0].arguments["sketch"] == "plate_profile"

    def test_expressions_inside_a_list_are_resolved(self) -> None:
        design = DesignSpec.of(
            "D",
            parameters=[Parameter("x_mm", Unit.MM, value=25.0)],
            features=[
                FeatureSpec("p.origin", "catia_point_at", {"at": [expr("x_mm"), 0, expr("x_mm * 2")]})
            ],
        )
        assert compile_spec(design).calls[-1].arguments["at"] == [25.0, 0, 50.0]

    def test_a_unit_mistake_is_caught_from_the_arguments_own_name(self) -> None:
        """98 registry parameters end in `_mm` and 36 in `_deg`. That is a check.

        `length_mm: "=draft_deg * 2"` builds a pad two units long and nothing
        errors. Here it is refused before CATIA is opened.
        """
        design = DesignSpec.of(
            "D",
            parameters=[Parameter("draft_deg", Unit.DEG, value=3.0)],
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "p.body",
                    "catia_pad",
                    {"sketch": ref("s.a"), "length_mm": expr("draft_deg * 2")},
                ),
            ],
        )
        with pytest.raises(UnitError, match="length_mm"):
            compile_spec(design)

    def test_a_forward_reference_says_to_move_the_feature(self) -> None:
        design = DesignSpec.of(
            "D",
            features=[
                FeatureSpec("p.body", "catia_pad", {"sketch": ref("s.a"), "length_mm": 5}),
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}),
            ],
        )
        with pytest.raises(DesignReferenceError, match="declared later"):
            compile_spec(design)

    def test_a_reference_to_nothing_lists_what_has_been_built(self) -> None:
        design = DesignSpec.of(
            "D",
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec("p.body", "catia_pad", {"sketch": ref("s.nope"), "length_mm": 5}),
            ],
        )
        with pytest.raises(DesignReferenceError, match="Built so far"):
            compile_spec(design)

    def test_a_malformed_reference_reads_as_a_reference_error(self) -> None:
        design = DesignSpec.of(
            "D",
            features=[FeatureSpec("p.body", "catia_pad", {"sketch": ref("Not A Name"), "length_mm": 5})],
        )
        with pytest.raises(DesignReferenceError):
            compile_spec(design)


class TestConditionalFeatures:
    def test_a_feature_whose_gate_is_false_is_not_built(self) -> None:
        plan = compile_spec(bracket())
        assert plan.suppressed == ("plate.window",)
        assert not plan.calls_for("plate.window")

    def test_it_is_built_when_the_gate_turns_true(self) -> None:
        """One parameter change, and the design regenerates with the feature in."""
        plan = compile_spec(bracket().set_parameter("thick_mm", 14.0))
        assert plan.suppressed == ()
        assert plan.calls_for("plate.window")

    def test_referring_to_a_suppressed_feature_says_the_condition_was_false(self) -> None:
        """"There is no pocket" is a mystery; naming the gate is an answer."""
        design = DesignSpec.of(
            "D",
            parameters=[Parameter("thick_mm", Unit.MM, value=4.0)],
            features=[
                FeatureSpec(
                    "s.a", "catia_sketch_create", {"support": "XY"}, when="thick_mm >= 12"
                ),
                FeatureSpec("p.body", "catia_pad", {"sketch": ref("s.a"), "length_mm": 5}),
            ],
        )
        with pytest.raises(DesignReferenceError, match="was not built"):
            compile_spec(design)

    def test_a_gate_that_is_a_quantity_rather_than_a_condition_is_refused(self) -> None:
        design = DesignSpec.of(
            "D",
            parameters=[Parameter("thick_mm", Unit.MM, value=4.0)],
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}, when="thick_mm")
            ],
        )
        with pytest.raises(UnitError, match="Compare it against"):
            compile_spec(design)


class TestValidationHappensHere:
    def test_an_unknown_operation_suggests_the_near_misses(self) -> None:
        design = DesignSpec.of("D", features=[FeatureSpec("a.b", "catia_pads")])
        with pytest.raises(FeatureError, match="Did you mean"):
            compile_spec(design)

    def test_an_argument_the_operation_does_not_take_lists_the_ones_it_does(self) -> None:
        design = DesignSpec.of(
            "D",
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "p.body",
                    "catia_pad",
                    {"sketch": ref("s.a"), "length_mm": 5, "colour": "red"},
                ),
            ],
        )
        with pytest.raises(FeatureError, match="It accepts"):
            compile_spec(design)

    def test_a_missing_required_argument_is_caught(self) -> None:
        design = DesignSpec.of("D", features=[FeatureSpec("s.a", "catia_sketch_create", {})])
        with pytest.raises(FeatureError, match="support"):
            compile_spec(design)

    def test_a_value_outside_the_schemas_bounds_is_caught(self) -> None:
        """The same validator the dispatcher runs, against the same schema.

        A looser check here would move the failure back to the workstation,
        which is the entire thing this compiler exists to stop.
        """
        design = DesignSpec.of(
            "D",
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec("p.body", "catia_pad", {"sketch": ref("s.a"), "length_mm": 0}),
            ],
        )
        with pytest.raises(FeatureError, match="greater than"):
            compile_spec(design)

    def test_an_enum_value_outside_the_closed_set_is_caught(self) -> None:
        design = DesignSpec.of(
            "D",
            features=[FeatureSpec("f.a", "catia_fillet", {"radius_mm": 2, "edges": "diagonal"})],
        )
        with pytest.raises(FeatureError, match="one of"):
            compile_spec(design)

    def test_the_late_bound_rename_is_not_rejected_for_shape(self) -> None:
        """`Created` is a string by the time the call runs; nothing here can check it."""
        plan = compile_spec(bracket())
        rename = plan.calls_for("plate.body")[1]
        assert isinstance(rename.arguments["feature"], Created)


class TestPolicy:
    def test_a_destructive_operation_has_no_place_in_a_replay(self) -> None:
        destructive = registry.by_tier(Tier.DESTRUCTIVE)
        assert destructive, "the registry has no destructive operation to test against"
        design = DesignSpec.of("D", features=[FeatureSpec("a.b", destructive[0].name)])
        with pytest.raises(PolicyError, match="destructive"):
            compile_spec(design)

    @pytest.mark.parametrize("tool", sorted(NOT_REPRODUCIBLE))
    def test_operations_that_would_make_a_build_unreproducible_are_refused(self, tool: str) -> None:
        design = DesignSpec.of("D", features=[FeatureSpec("a.b", tool)])
        with pytest.raises(PolicyError) as caught:
            compile_spec(design)
        # The reason is carried, not implied: an over-refusal has to be arguable,
        # because the agent's recovery from one is to try something else.
        assert NOT_REPRODUCIBLE[tool].split(",")[0][:20] in str(caught.value)

    def test_a_server_only_operation_builds_nothing(self) -> None:
        server_only = sorted(registry.SERVER_ONLY)
        assert server_only, "the registry has no server-only operation to test against"
        design = DesignSpec.of("D", features=[FeatureSpec("a.b", server_only[0])])
        with pytest.raises(PolicyError, match="builds nothing"):
            compile_spec(design)


class TestDeterminism:
    def test_the_same_spec_compiles_to_the_same_plan(self) -> None:
        assert compile_spec(bracket()).digest() == compile_spec(bracket()).digest()

    def test_the_plan_digest_moves_when_a_dimension_moves(self) -> None:
        before = compile_spec(bracket())
        after = compile_spec(bracket().set_parameter("thick_mm", 10.0))
        assert after.digest() != before.digest()

    def test_the_plan_carries_the_spec_digest_it_came_from(self) -> None:
        """Provenance in one field: which design produced this build (D11)."""
        design = bracket()
        assert compile_spec(design).spec_digest == design.digest()

    def test_a_plan_serialises_with_its_late_bound_values_intact(self) -> None:
        plan = compile_spec(bracket())
        rename = next(c for c in plan.to_dict()["calls"] if c["tool"] == "catia_feature_rename")
        assert rename["arguments"]["feature"] == {"$created": "plate.body"}

    def test_argument_keys_are_emitted_sorted(self) -> None:
        plan = compile_spec(bracket())
        pad = next(c for c in plan.to_dict()["calls"] if c["tool"] == "catia_pad")
        assert list(pad["arguments"]) == sorted(pad["arguments"])

    def test_two_specs_differing_only_in_a_parameter_name_build_the_same_thing(self) -> None:
        """The plan digest answers 'does this build the same part?'.

        The spec digest answers a different question, and both are worth having:
        rename a parameter that only appears inside one formula and the geometry
        is byte-identical while the design is not.
        """
        design = bracket()
        renamed = design.with_parameters(
            [
                Parameter("width_mm", Unit.MM, value=120.0),
                Parameter("depth_mm", Unit.MM, value=80.0),
                Parameter("t_mm", Unit.MM, value=8.0),
                Parameter("fillet_mm", Unit.MM, expression="t_mm / 2"),
                Parameter("lighten", Unit.NONE, expression="t_mm >= 12"),
            ]
        ).with_features(
            [
                f
                if "thick_mm" not in str(dict(f.args)) and f.when != "lighten"
                else FeatureSpec(
                    f.name,
                    f.op,
                    {k: (expr("t_mm") if v == expr("thick_mm") else v) for k, v in f.args.items()},
                    when=f.when,
                    note=f.note,
                )
                for f in design.features
            ]
        )
        assert renamed.digest() != design.digest()
        assert compile_spec(renamed).digest() == compile_spec(design).digest()


class TestLateBinding:
    def test_bind_resolves_a_created_value_from_the_results_so_far(self) -> None:
        plan = compile_spec(bracket())
        rename = plan.calls_for("plate.body")[1]
        assert bind(rename.arguments, {"plate.body": "Pad.1"}) == {
            "feature": "Pad.1",
            "name": "plate_body",
        }

    def test_literals_pass_through_untouched(self) -> None:
        assert bind({"length_mm": 8.0, "sketch": "s"}, {}) == {"length_mm": 8.0, "sketch": "s"}

    def test_binding_before_the_creating_call_has_run_says_so(self) -> None:
        plan = compile_spec(bracket())
        rename = plan.calls_for("plate.body")[1]
        with pytest.raises(DesignReferenceError, match="no result has been recorded"):
            bind(rename.arguments, {})


class TestThePlanIsReadable:
    def test_a_features_rationale_is_carried_into_the_plan(self) -> None:
        """H5: "why is this rib here" must have an answer in six months."""
        plan = compile_spec(bracket())
        assert plan.calls_for("plate.body")[0].note == "Extrude the footprint to thickness."

    def test_calls_are_indexed_in_order(self) -> None:
        plan = compile_spec(bracket())
        assert [call.index for call in plan.calls] == list(range(len(plan)))

    def test_the_plan_can_say_what_a_feature_will_be_called(self) -> None:
        assert compile_spec(bracket()).catia_name("plate.body") == "plate_body"


class TestTheRenameCallIsNotTakenOnTrust:
    """The one call no author reviewed, and the only one carrying a late-bound
    value. It goes through the same validator as everything else."""

    def test_the_rename_operation_is_looked_up_rather_than_assumed(self) -> None:
        """If `catia_feature_rename` vanished, B2 would quietly stop applying to
        every Part Design feature and the first sign would be a design whose
        references no longer resolved."""
        from app.design.compile import _RENAME

        assert _RENAME is registry.get("catia_feature_rename")

    def test_a_late_bound_argument_does_not_trip_the_required_check(self) -> None:
        """`feature` is required and is a `Created` at compile time. Dropping it
        from `required` must not also drop the *literal* `name` beside it."""
        plan = compile_spec(bracket())
        rename = plan.calls_for("plate.body")[1]
        assert set(rename.arguments) == {"feature", "name"}

    def test_a_missing_literal_argument_is_still_caught_beside_a_late_bound_one(self) -> None:
        from app.catia.ops import registry as reg
        from app.design.compile import Created, _validate

        with pytest.raises(FeatureError, match="name"):
            _validate(
                FeatureSpec("a.b", "catia_pad"),
                reg.OPERATIONS_BY_NAME["catia_feature_rename"],
                {"feature": Created("a.b")},
            )


class TestErrorsCarryTheirLocation:
    def test_an_expression_error_names_the_feature_and_the_argument(self) -> None:
        """Thirty features in, "unknown parameter" without a location is a search."""
        design = DesignSpec.of(
            "D",
            features=[
                FeatureSpec("s.a", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "p.body", "catia_pad", {"sketch": ref("s.a"), "length_mm": expr("nope")}
                ),
            ],
        )
        with pytest.raises(Exception) as caught:
            compile_spec(design)
        assert "p.body.length_mm" in str(caught.value)


class TestPlanReadout:
    def test_a_plan_is_iterable_and_countable(self) -> None:
        plan = compile_spec(bracket())
        assert len(list(plan)) == len(plan)

    def test_a_plan_serialises_to_json(self) -> None:
        import json

        plan = compile_spec(bracket())
        loaded = json.loads(plan.to_json())
        assert loaded["design"] == "Bracket"
        assert len(loaded["calls"]) == len(plan)

    def test_a_list_argument_survives_serialisation(self) -> None:
        import json

        design = DesignSpec.of(
            "D",
            parameters=[Parameter("x_mm", Unit.MM, value=25.0)],
            features=[
                FeatureSpec("p.origin", "catia_point_at", {"at": [expr("x_mm"), 0, 5]})
            ],
        )
        loaded = json.loads(compile_spec(design).to_json())
        assert loaded["calls"][-1]["arguments"]["at"] == [25.0, 0, 5]
