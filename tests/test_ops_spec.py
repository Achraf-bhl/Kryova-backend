"""What an operation declaration refuses, and why each refusal is load-bearing.

`app/catia/ops/spec.py` is the vocabulary the 201 operations are written in, and
it validates itself at import: a malformed declaration raises when the module is
loaded rather than when a call is made. That is the right design and it had one
consequence — none of the refusals had ever run in a test, because every
declaration in the tree is correct. So the guard rails were untested, and the
first person to add a bad declaration would have found out from a wrong part
instead of from an import error.

The asymmetry between `supplied_by_server` and `consumed_by_server` gets the
most attention here because it is the one that has already been a live bug in
both directions:

* a server-supplied field left *in* the model-facing schema meant the model
  could set the density every reported mass is computed from;
* the same field added *before* validation meant the model-facing schema
  rejected the server's own key, every call fell back to CATIA's 1000 kg/m³
  default, and the unit tests — which drove only the daemon's schema — passed.

Offline: no database fixture, no CATIA.
"""

import pytest

from app.catia.ops import limits
from app.catia.ops.spec import (
    Operation,
    Param,
    Tier,
    Workbench,
    angle,
    bounded_number,
    coordinate,
    count,
    direction3,
    flag,
    for_server,
    from_server,
    length,
    name_pair,
    one_of,
    optional,
    point2,
    point3,
    point_list,
    ratio,
    raw,
    required,
    text,
    thickness,
    tilt,
)


def an_operation(**overrides: object) -> Operation:
    fields: dict[str, object] = {
        "name": "catia_thing",
        "summary": "A test operation.",
        "tier": Tier.WRITE,
        "workbench": Workbench.PART_DESIGN,
        "params": (required("length_mm", length("How far.")),),
    }
    fields.update(overrides)
    return Operation(**fields)  # type: ignore[arg-type]


class TestParamRefusals:
    def test_a_parameter_cannot_be_both_supplied_and_consumed_by_the_server(self) -> None:
        """The first means the model may not send it, the second that it must."""
        with pytest.raises(ValueError, match="cannot be both supplied and consumed"):
            Param(
                name="density_kg_m3",
                schema={"type": "number", "description": "x"},
                supplied_by_server=True,
                consumed_by_server=True,
            )

    @pytest.mark.parametrize("name", ["length mm", "2length", "length-mm", "", "class-of"])
    def test_a_name_that_is_not_an_identifier_is_refused(self, name: str) -> None:
        with pytest.raises(ValueError, match="not a valid identifier"):
            Param(name=name, schema={"type": "number", "description": "x"})

    def test_a_parameter_with_no_description_is_refused(self) -> None:
        """The model reads these to choose values; an undescribed parameter is
        one it will guess at."""
        with pytest.raises(ValueError, match="no description"):
            Param(name="length_mm", schema={"type": "number"})

    def test_a_well_formed_parameter_is_accepted(self) -> None:
        param = Param(name="length_mm", schema=length("How far."))
        assert param.required is False
        assert "Millimetres" in param.schema["description"]


class TestOperationRefusals:
    def test_a_tool_name_must_carry_the_catia_prefix(self) -> None:
        """The prefix means 'goes to the workstation'. A tool without it is
        answered by the server, and the two must not be confusable."""
        with pytest.raises(ValueError, match="must start with 'catia_'"):
            an_operation(name="pad")

    def test_a_server_only_operation_must_not_name_a_backend_method(self) -> None:
        with pytest.raises(ValueError, match="no device call behind it"):
            an_operation(server_only=True, method="thing")

    def test_a_server_only_operation_with_no_method_is_accepted(self) -> None:
        assert an_operation(server_only=True).method == ""

    def test_the_method_defaults_to_the_name_without_its_prefix(self) -> None:
        assert an_operation(name="catia_pad_thing").method == "pad_thing"

    def test_an_explicit_method_is_kept(self) -> None:
        assert an_operation(method="elsewhere").method == "elsewhere"

    def test_a_duplicate_parameter_is_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate parameter"):
            an_operation(
                params=(
                    required("length_mm", length("How far.")),
                    optional("length_mm", length("How far again.")),
                )
            )

    def test_a_parameter_that_is_also_a_server_field_is_refused(self) -> None:
        """A field the server fills must not also be model-writable."""
        with pytest.raises(ValueError, match="both a model parameter and a server field"):
            an_operation(server_fields=("length_mm",))


class TestTheServerFieldAsymmetry:
    """The bug that has already happened twice, pinned in both directions."""

    def _operation(self) -> Operation:
        return an_operation(
            params=(
                required("material", {"type": "string", "description": "The material."}),
                from_server("density_kg_m3", {"type": "number", "description": "kg/m3."}),
            )
        )

    def test_the_model_never_sees_a_server_supplied_field(self) -> None:
        assert "density_kg_m3" not in self._operation().json_schema()["properties"]

    def test_the_daemon_requires_it(self) -> None:
        """By the time the frame reaches the daemon the server has filled it in,
        and its absence is itself a fault worth refusing."""
        schema = self._operation().daemon_schema()
        assert "density_kg_m3" in schema["properties"]
        assert "density_kg_m3" in schema["required"]

    def test_server_supplied_fields_lists_exactly_those(self) -> None:
        assert self._operation().server_supplied_fields == ("density_kg_m3",)

    def _consuming(self) -> Operation:
        return an_operation(
            params=(
                for_server("checkpoint_id", {"type": "string", "description": "Which one."}),
                required("length_mm", length("How far.")),
            ),
            server_fields=("checkpoint",),
        )

    def test_the_model_must_supply_a_server_consumed_field(self) -> None:
        schema = self._consuming().json_schema()
        assert "checkpoint_id" in schema["properties"]
        assert "checkpoint_id" in schema["required"]

    def test_the_daemon_never_sees_it(self) -> None:
        """The server resolves it into a `server_fields` key and never forwards
        it; a daemon schema that required the original would refuse every call."""
        assert "checkpoint_id" not in self._consuming().daemon_schema()["properties"]


class TestTier:
    @pytest.mark.parametrize(
        ("tier", "mutating"),
        [(Tier.READ, False), (Tier.WRITE, True), (Tier.DESTRUCTIVE, True)],
    )
    def test_mutating_is_everything_that_is_not_a_read(self, tier: Tier, mutating: bool) -> None:
        assert an_operation(tier=tier).mutating is mutating


class TestParameterConstructors:
    """Each names a kind of quantity and carries that kind's bound and unit.

    The unit is appended by the constructor precisely so no call site can forget
    it or contradict it — a model that has to infer millimetres from context is
    a model that will eventually infer inches.
    """

    def test_a_length_is_positive_bounded_and_says_millimetres(self) -> None:
        schema = length("How far.")
        assert schema["exclusiveMinimum"] == 0
        assert schema["maximum"] == limits.MAX_LENGTH_MM
        assert schema["description"].endswith("Millimetres.")

    def test_a_thickness_is_bounded_tighter_than_a_length(self) -> None:
        assert thickness("Wall.")["maximum"] == limits.MAX_THICKNESS_MM
        assert thickness("Wall.")["maximum"] < length("How far.")["maximum"]

    def test_a_coordinate_may_be_negative(self) -> None:
        schema = coordinate("Where.")
        assert schema["minimum"] == limits.MIN_COORD_MM
        assert "signed" in schema["description"]

    def test_a_tilt_stops_short_of_degenerate(self) -> None:
        """90 degrees of draft is not a draft."""
        assert tilt("Draft.")["maximum"] == limits.MAX_TILT_DEG
        assert tilt("Draft.")["maximum"] < angle("Sweep.")["maximum"]

    def test_a_ratio_is_centred_on_identity(self) -> None:
        schema = ratio("Scale.")
        assert schema["minimum"] <= 1.0 <= schema["maximum"]
        assert "1.0" in schema["description"]

    def test_a_count_is_a_whole_number_with_a_ceiling(self) -> None:
        schema = count("How many.")
        assert schema["type"] == "integer"
        assert schema["maximum"] == limits.MAX_INSTANCES

    def test_a_closed_choice_is_closed(self) -> None:
        """An open string is a value the daemon has to guess at, and a guess in
        CAD is a wrong part rather than an error message."""
        assert one_of(("a", "b"), "Pick.")["enum"] == ["a", "b"]

    def test_a_point_is_three_bounded_numbers(self) -> None:
        schema = point3("Where.")
        assert schema["minItems"] == schema["maxItems"] == 3
        assert schema["items"]["maximum"] == limits.MAX_COORD_MM

    def test_a_sketch_point_is_two_numbers_in_the_sketch_frame(self) -> None:
        schema = point2("Where.")
        assert schema["minItems"] == schema["maxItems"] == 2
        assert "sketch's" in schema["description"]

    def test_a_direction_needs_no_normalising_but_refuses_zero(self) -> None:
        assert "length is ignored" in direction3("Which way.")["description"]

    def test_a_point_list_is_bounded_by_the_point_ceiling(self) -> None:
        assert point_list("A polyline.")["maxItems"] == limits.MAX_POINTS

    def test_a_pair_is_exactly_two_rather_than_at_least_two(self) -> None:
        """A corner between three elements is not a corner, and the schema
        should say so rather than let the daemon discover it."""
        schema = name_pair("Two elements.")
        assert schema["minItems"] == schema["maxItems"] == 2

    def test_a_flag_is_a_boolean(self) -> None:
        assert flag("On or off.")["type"] == "boolean"

    def test_free_text_is_bounded(self) -> None:
        assert text("A note.")["maxLength"] == limits.MAX_TEXT_CHARS

    def test_a_bounded_number_appends_its_unit(self) -> None:
        schema = bounded_number("Pressure.", minimum=0, maximum=10, unit="MPa")
        assert schema["description"].endswith("MPa.")

    def test_a_bounded_number_with_no_unit_says_nothing_extra(self) -> None:
        assert bounded_number("Just a number.", minimum=0, maximum=1)["description"] == (
            "Just a number."
        )


class TestRawIsAnEscapeHatchNotADoor:
    def test_it_still_insists_on_a_description(self) -> None:
        with pytest.raises(ValueError, match="still need a description"):
            raw({"type": "string"})

    def test_it_copies_rather_than_aliasing_the_caller_s_dict(self) -> None:
        """A shared dict would let one operation's schema mutate another's."""
        source = {"type": "string", "description": "x"}
        produced = raw(source)
        produced["maxLength"] = 3
        assert "maxLength" not in source
