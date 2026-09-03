"""The design specification as an artefact: structure, round-tripping, identity.

A spec is the thing that makes a design diffable, replayable and testable, and
all three of those rest on it being *stable*: the same design must serialise the
same way and hash the same way on any machine, or `digest()` is decoration and
roadmap I5 is unfalsifiable.

The other half of what is pinned here is that this module knows nothing about
the operation registry. A spec must be loadable, diffable and storable by code
that has no business importing the CATIA layer — the registry check happens in
`compile`, which is the only place that can do it properly anyway.

Offline: no database fixture, no CATIA, no gmsh.
"""

import json

import pytest

from app.design.errors import FeatureError, SpecError
from app.design.params import Parameter, Unit
from app.design.spec import (
    FORMAT_VERSION,
    DesignSpec,
    FeatureSpec,
    expr,
    expression_source,
    is_expression,
    is_reference,
    ref,
    reference_target,
    refs,
)


def _plate() -> DesignSpec:
    return DesignSpec.of(
        "Plate",
        material="steel-1018",
        description="A flat plate, for testing.",
        parameters=[
            Parameter("width_mm", Unit.MM, value=100.0, description="Across."),
            Parameter("thick_mm", Unit.MM, value=6.0),
            Parameter("fillet_mm", Unit.MM, expression="thick_mm / 2"),
        ],
        features=[
            FeatureSpec("plate.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "plate.body",
                "catia_pad",
                {"sketch": ref("plate.profile"), "length_mm": expr("thick_mm")},
                note="Extrude to thickness.",
            ),
        ],
    )


class TestArgumentMarkers:
    """`=` means evaluate, `@` means another feature. Both have to be unambiguous.

    Without a marker, `{"sketch": "plate.profile"}` is ambiguous between a
    semantic name and a CATIA element that happens to be called that — and
    resolving it the wrong way puts the feature on the wrong geometry without
    erroring, which is the class of failure this package exists to remove.
    """

    def test_expressions_are_recognised_and_unwrapped(self) -> None:
        assert is_expression(expr("a + 1"))
        assert expression_source(expr("a + 1")) == "a + 1"

    def test_references_are_recognised_and_unwrapped(self) -> None:
        assert is_reference(ref("plate.body"))
        assert reference_target(ref("plate.body")) == "plate.body"

    def test_a_plain_string_is_neither(self) -> None:
        assert not is_expression("XY")
        assert not is_reference("XY")

    def test_non_strings_are_neither_rather_than_raising(self) -> None:
        for value in (12.0, None, True, ["a"]):
            assert not is_expression(value)
            assert not is_reference(value)

    def test_refs_writes_a_list(self) -> None:
        assert refs(["a.b", "c.d"]) == ["@a.b", "@c.d"]


class TestFeatureSpec:
    def test_a_malformed_semantic_name_is_refused_where_it_is_written(self) -> None:
        with pytest.raises(SpecError):
            FeatureSpec("Plate.Body", "catia_pad")

    def test_an_operation_name_is_required(self) -> None:
        with pytest.raises(FeatureError, match="operation name"):
            FeatureSpec("plate.body", "")

    def test_arguments_must_be_a_mapping_with_string_keys(self) -> None:
        with pytest.raises(FeatureError, match="mapping"):
            FeatureSpec("plate.body", "catia_pad", ["not", "a", "mapping"])  # type: ignore[arg-type]
        with pytest.raises(FeatureError, match="strings"):
            FeatureSpec("plate.body", "catia_pad", {1: "x"})  # type: ignore[dict-item]

    def test_this_module_does_not_check_the_operation_exists(self) -> None:
        """Deliberate: a spec is loadable without importing the CATIA layer.

        The check is not lost — `compile` does it, and does it better, because
        by then the arguments have been resolved and can be checked too.
        """
        assert FeatureSpec("plate.body", "catia_not_a_real_operation").op

    def test_canonical_form_sorts_argument_keys(self) -> None:
        feature = FeatureSpec("a.b", "catia_pad", {"z": 1, "a": 2})
        assert list(feature.to_dict()["args"]) == ["a", "z"]

    def test_unset_optional_keys_are_omitted(self) -> None:
        assert FeatureSpec("a.b", "catia_pad").to_dict() == {"name": "a.b", "op": "catia_pad"}


class TestDesignSpec:
    def test_a_design_needs_a_name(self) -> None:
        with pytest.raises(SpecError, match="needs a name"):
            DesignSpec.of("   ")

    def test_two_features_cannot_share_a_name(self) -> None:
        with pytest.raises(FeatureError, match="both called"):
            DesignSpec.of(
                "D",
                features=[
                    FeatureSpec("a.b", "catia_pad"),
                    FeatureSpec("a.b", "catia_pocket"),
                ],
            )

    def test_feature_order_is_build_order_and_is_preserved(self) -> None:
        design = _plate()
        assert design.feature_names() == ("plate.profile", "plate.body")

    def test_looking_up_a_missing_feature_lists_the_features(self) -> None:
        with pytest.raises(FeatureError, match="plate.body"):
            _plate().feature("plate.nope")


class TestEditing:
    """Specs are frozen; an edit is a copy. That is what makes a diff possible."""

    def test_setting_a_parameter_returns_a_new_spec(self) -> None:
        before = _plate()
        after = before.set_parameter("thick_mm", 10.0)
        assert before.parameters.resolve().number("thick_mm") == pytest.approx(6.0)
        assert after.parameters.resolve().number("thick_mm") == pytest.approx(10.0)

    def test_a_derived_parameter_moves_with_the_one_it_reads(self) -> None:
        after = _plate().set_parameter("thick_mm", 10.0)
        assert after.parameters.resolve().number("fillet_mm") == pytest.approx(5.0)

    def test_overwriting_a_derived_parameter_is_refused(self) -> None:
        """Otherwise the formula stays in the spec, unused and untrue."""
        with pytest.raises(SpecError, match="derived from"):
            _plate().set_parameter("fillet_mm", 99.0)

    def test_setting_an_unknown_parameter_lists_the_known_ones(self) -> None:
        with pytest.raises(SpecError, match="Declared"):
            _plate().set_parameter("nope_mm", 1.0)


class TestSerialisation:
    def test_a_design_round_trips_through_json(self) -> None:
        before = _plate()
        after = DesignSpec.from_json(before.to_json())
        assert after.to_dict() == before.to_dict()
        assert after.digest() == before.digest()

    def test_the_serialised_form_carries_a_format_version(self) -> None:
        assert json.loads(_plate().to_json())["format_version"] == FORMAT_VERSION

    def test_an_unknown_format_version_is_refused_rather_than_guessed_at(self) -> None:
        data = _plate().to_dict()
        data["format_version"] = 99
        with pytest.raises(SpecError, match="format version"):
            DesignSpec.from_dict(data)

    def test_an_unknown_top_level_key_is_refused(self) -> None:
        """A key this build does not understand is one it would silently drop."""
        data = _plate().to_dict()
        data["fixtures"] = []
        with pytest.raises(SpecError, match="unknown top-level"):
            DesignSpec.from_dict(data)

    def test_an_unknown_feature_key_is_refused(self) -> None:
        data = _plate().to_dict()
        data["features"][0]["colour"] = "red"
        with pytest.raises(FeatureError, match="unknown keys"):
            DesignSpec.from_dict(data)

    def test_an_unknown_parameter_unit_names_the_allowed_ones(self) -> None:
        data = _plate().to_dict()
        data["parameters"][0]["unit"] = "inch"
        with pytest.raises(SpecError, match="Allowed"):
            DesignSpec.from_dict(data)

    def test_invalid_json_reads_as_a_spec_error(self) -> None:
        with pytest.raises(SpecError, match="not valid JSON"):
            DesignSpec.from_json("{oops")

    def test_a_json_array_is_not_a_design(self) -> None:
        with pytest.raises(SpecError, match="JSON object"):
            DesignSpec.from_json("[]")


class TestIdentity:
    def test_the_digest_is_stable_across_construction_order(self) -> None:
        """Two specs that differ only in how a dict literal was written match.

        `to_dict` sorts argument keys precisely so this is true — otherwise the
        digest would move for a reason that changes nothing about the part.
        """
        one = DesignSpec.of(
            "D", features=[FeatureSpec("a.b", "catia_pad", {"sketch": "S", "length_mm": 3})]
        )
        two = DesignSpec.of(
            "D", features=[FeatureSpec("a.b", "catia_pad", {"length_mm": 3, "sketch": "S"})]
        )
        assert one.digest() == two.digest()

    def test_the_digest_moves_when_a_dimension_moves(self) -> None:
        before = _plate()
        assert before.set_parameter("thick_mm", 10.0).digest() != before.digest()

    def test_the_digest_moves_when_a_feature_is_reordered(self) -> None:
        """Order is the design: two features swapped build a different tree."""
        design = _plate()
        swapped = design.with_features(tuple(reversed(design.features)))
        assert swapped.digest() != design.digest()

    def test_the_digest_covers_the_note(self) -> None:
        """Rationale is part of the design record, so a change to it is a change."""
        design = _plate()
        renoted = design.with_features(
            [design.features[0], FeatureSpec("plate.body", "catia_pad", dict(design.features[1].args), note="different")]
        )
        assert renoted.digest() != design.digest()
