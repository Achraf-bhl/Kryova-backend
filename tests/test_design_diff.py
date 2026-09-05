"""What changed between two revisions, and how far the change reaches.

B4. Named after the failures they prevent:

* **A parameter nothing reads rebuilds nothing.** The comparison happens after
  compilation, so this falls out of the design rather than needing a rule.
* **A change reaches through a sketch.** Widen a rectangle and the pad built on
  it is a different shape while its own call is byte-identical. Following `@`
  references alone misses that, and missing it is how a stale simulation gets
  presented as current.
* **A rebuild that changes nothing is not triggered.** The plan digest, not the
  spec digest — renaming a parameter used in one formula builds the same part.

Offline: no database fixture, no CATIA, no gmsh.
"""

import pytest

from app.design.compile import compile_spec
from app.design.diff import diff_plans, diff_specs
from app.design.errors import SpecError
from app.design.params import Parameter, Unit
from app.design.spec import DesignSpec, FeatureSpec, expr, ref
from tests.test_design_compile import bracket


class TestWhatChanged:
    def test_a_changed_parameter_is_reported_with_both_sides(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("thick_mm", 12.0))

        assert len(diff.parameters) == 1
        change = diff.parameters[0]
        assert change.name == "thick_mm"
        assert "8" in str(change.before)
        assert "12" in str(change.after)

    def test_identical_designs_are_falsey(self) -> None:
        diff = diff_specs(bracket(), bracket())

        assert not diff
        assert not diff.plan_changed
        assert diff.affected == ()
        assert "nothing that affects the built part changed" in diff.summary()

    def test_a_material_change_is_reported(self) -> None:
        before = bracket()
        after = DesignSpec(
            name=before.name,
            parameters=before.parameters,
            features=before.features,
            material="steel-1018",
        )

        diff = diff_specs(before, after)

        assert diff.material is not None
        assert diff.material.after == "steel-1018"

    def test_an_added_feature_is_reported_as_added(self) -> None:
        before = bracket()
        after = before.with_features(
            [*before.features, FeatureSpec("plate.extra", "catia_sketch_create", {"support": "YZ"})]
        )

        diff = diff_specs(before, after)

        kinds = {change.name: change.kind for change in diff.features}
        assert kinds["plate.extra"] == "added"
        assert "plate.extra" in diff.changed_calls

    def test_a_removed_feature_is_reported_as_removed(self) -> None:
        before = bracket()
        after = before.with_features(before.features[:-1])

        diff = diff_specs(before, after)

        kinds = {change.name: change.kind for change in diff.features}
        assert kinds["plate.window"] == "removed"

    def test_a_feature_that_starts_being_built_is_reported_as_unsuppressed(self) -> None:
        """`plate.window` is gated on `lighten`, which is `thick_mm >= 12`."""
        diff = diff_specs(bracket(), bracket().set_parameter("thick_mm", 12.0))

        kinds = {change.name: change.kind for change in diff.features}
        assert kinds["plate.window"] == "unsuppressed"

    def test_a_feature_that_stops_being_built_is_reported_as_suppressed(self) -> None:
        thick = bracket().set_parameter("thick_mm", 12.0)

        diff = diff_specs(thick, bracket())

        kinds = {change.name: change.kind for change in diff.features}
        assert kinds["plate.window"] == "suppressed"

    def test_editing_only_a_note_changes_nothing(self) -> None:
        """A diff that shouts because someone improved a comment stops being read."""
        before = bracket()
        after = before.with_features(
            [
                FeatureSpec(f.name, f.op, f.args, when=f.when, note="rewritten rationale")
                for f in before.features
            ]
        )

        diff = diff_specs(before, after)

        assert not diff.plan_changed
        assert diff.affected == ()


class TestHowFarItReaches:
    def test_a_parameter_nothing_reads_reaches_nothing(self) -> None:
        """The payoff of comparing compiled plans rather than spec text."""
        before = bracket()
        after = before.with_parameters(
            [*before.parameters, Parameter("unused_mm", Unit.MM, value=99.0)]
        )

        diff = diff_specs(before, after)

        assert diff.parameters, "the parameter itself did change"
        assert not diff.plan_changed, "but it builds exactly the same part"
        assert diff.affected == ()

    def test_a_change_reaches_the_feature_that_reads_it(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("thick_mm", 12.0))

        assert "plate.body" in diff.changed_calls, "the pad's length is thick_mm"
        assert "plate.edges" in diff.changed_calls, "the fillet radius is derived from it"

    def test_a_change_reaches_through_a_sketch_into_the_solid(self) -> None:
        """The case a reference walk alone gets wrong.

        `width_mm` is read only by the rectangle. The pad extrudes the *sketch*,
        so its own call is byte-identical — and it comes out a different shape.
        Reporting only the rectangle here would leave a stale pad looking
        current, which is precisely what invalidates a simulation nobody re-ran.
        """
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))

        assert diff.changed_calls == ("plate.outline",)
        assert "plate.body" in diff.downstream
        assert "plate.edges" in diff.downstream, "and on to what is built on the pad"

    def test_the_two_halves_are_reported_separately(self) -> None:
        """One was edited; the other was not and may still be a different shape."""
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))

        assert set(diff.changed_calls).isdisjoint(diff.downstream)
        assert set(diff.affected) == set(diff.changed_calls) | set(diff.downstream)

    def test_affected_is_in_build_order(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))

        order = [f.name for f in bracket().features]
        assert list(diff.affected) == sorted(diff.affected, key=order.index)


class TestInvalidation:
    def test_a_result_that_read_an_affected_feature_is_invalidated(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))

        assert diff.invalidates(["plate.body"])

    def test_a_result_that_read_nothing_affected_survives(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))

        assert not diff.invalidates(["plate.profile"])

    def test_a_whole_part_result_is_invalidated_by_any_change(self) -> None:
        """An empty dependency list means "this read the part", the conservative reading."""
        changed = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))
        unchanged = diff_specs(bracket(), bracket())

        assert changed.invalidates([])
        assert not unchanged.invalidates([])

    def test_affects_answers_for_one_feature(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))

        assert diff.affects("plate.body")
        assert not diff.affects("plate.profile")


class TestTheReport:
    def test_the_summary_names_the_change_and_the_reach(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))
        summary = diff.summary()

        assert "width_mm" in summary
        assert "Rebuilds 3 feature(s)" in summary
        assert "plate.body" in summary, "the un-edited features are the surprising ones"

    def test_the_design_name_appears_once(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("thick_mm", 12.0))

        assert diff.summary().count("Bracket") == 1

    def test_it_serialises(self) -> None:
        diff = diff_specs(bracket(), bracket().set_parameter("width_mm", 200.0))
        data = diff.to_dict()

        assert data["plan_changed"] is True
        assert data["changed_calls"] == ["plate.outline"]
        assert "plate.body" in data["downstream"]

    def test_diff_plans_accepts_plans_already_compiled(self) -> None:
        """A regeneration loop has just compiled the new plan; compiling twice is waste."""
        before, after = bracket(), bracket().set_parameter("thick_mm", 12.0)

        from_specs = diff_specs(before, after)
        from_plans = diff_plans(before, after, compile_spec(before), compile_spec(after))

        assert from_specs.to_dict() == from_plans.to_dict()

    def test_a_spec_that_does_not_compile_raises_rather_than_diffing(self) -> None:
        """A diff against a broken spec is a compile error wearing a diff's clothes."""
        broken = bracket().with_features(
            [*bracket().features, FeatureSpec("x", "catia_not_a_real_op", {})]
        )

        with pytest.raises(SpecError):
            diff_specs(bracket(), broken)


class TestReferencesAreFoundWhereverTheyAre:
    def test_a_reference_nested_in_a_list_still_counts(self) -> None:
        """References are walked at any depth; a shallow scan would miss these."""
        design = DesignSpec.of(
            "Nested",
            parameters=[Parameter("r_mm", Unit.MM, value=2.0)],
            features=[
                FeatureSpec("s", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "rect",
                    "catia_sketch_rectangle",
                    {"sketch": ref("s"), "width_mm": expr("r_mm * 10"), "height_mm": 20.0},
                ),
                FeatureSpec("pad", "catia_pad", {"sketch": ref("s"), "length_mm": 5.0}),
            ],
        )
        wider = design.set_parameter("r_mm", 4.0)

        diff = diff_specs(design, wider)

        assert diff.changed_calls == ("rect",)
        assert "pad" in diff.downstream
