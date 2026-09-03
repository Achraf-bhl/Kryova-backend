"""The parameter graph: arithmetic, dimensions, ordering, and what is refused.

Three failures are pinned here, in rough order of how expensive each is.

**A dimension error that resolves.** `pad_length_mm = draft_deg * 2` is a number
and CATIA will happily build a pad two units long. Nothing in this codebase
converts units, so the only place that can be caught is where the quantities are
combined, which is this module.

**A cycle that hangs or half-resolves.** A parameter graph with a loop has no
answer, and the useful report is *which* loop — the author's next move is to
break exactly one of those edges.

**An expression that is not arithmetic.** Expressions come from a language model
via a design document. They are evaluated from a parsed AST against a whitelist,
never `eval`, and the whitelist is asserted here rather than assumed.

Offline: no database fixture, no CATIA, no gmsh.
"""

import math

import pytest

from app.catia.ops import vocabulary
from app.design.errors import CycleError, ExpressionError, ParameterError, UnitError
from app.design.params import (
    DIMENSIONLESS,
    UNIT_DIMENSIONS,
    Dimension,
    Parameter,
    ParameterSet,
    Quantity,
    Unit,
    dependencies,
    evaluate,
)

MM = UNIT_DIMENSIONS[Unit.MM]
DEG = UNIT_DIMENSIONS[Unit.DEG]
NEWTON = UNIT_DIMENSIONS[Unit.NEWTON]
MPA = UNIT_DIMENSIONS[Unit.MPA]


def q(value: float, unit: Unit = Unit.NONE) -> Quantity:
    return Quantity.of(value, unit)


class TestUnitsStayAlignedWithCatia:
    def test_the_unit_set_is_exactly_what_a_catia_parameter_can_carry(self) -> None:
        """Drift here would need a translation table nobody would remember to write.

        `params` asserts this at import; the test states it so the failure has a
        name rather than arriving as "some module failed to import".
        """
        assert {unit.value for unit in Unit} == set(vocabulary.PARAMETER_UNITS)

    def test_every_unit_has_a_distinct_dimension(self) -> None:
        """The reverse map that error messages read must be unambiguous."""
        dimensions = [UNIT_DIMENSIONS[unit] for unit in Unit]
        assert len(set(dimensions)) == len(dimensions)

    def test_mpa_is_newtons_per_square_millimetre(self) -> None:
        """Not a base of its own — which is what makes force/area check out."""
        assert MPA == NEWTON / Dimension(length=2)


class TestDimensionAlgebra:
    def test_multiplying_lengths_gives_an_area(self) -> None:
        assert (MM * MM) == UNIT_DIMENSIONS[Unit.MM2]

    def test_dividing_force_by_area_gives_a_stress(self) -> None:
        assert (NEWTON / UNIT_DIMENSIONS[Unit.MM2]) == MPA

    def test_the_root_of_an_odd_dimension_is_refused(self) -> None:
        """`sqrt(length)` is not a quantity this system can name."""
        with pytest.raises(UnitError, match="root"):
            MM.root(2)

    def test_a_dimension_renders_as_a_unit_when_it_has_one(self) -> None:
        assert str(MM) == "'mm'"
        assert str(DIMENSIONLESS) == "a plain number"
        assert "mm" in str(MM * NEWTON)


class TestEvaluation:
    def test_arithmetic_carries_the_dimension(self) -> None:
        values = {"w": q(120, Unit.MM), "d": q(80, Unit.MM)}
        area = evaluate("w * d", values)
        assert area.value == pytest.approx(9600.0)
        assert area.dimension == UNIT_DIMENSIONS[Unit.MM2]

    def test_a_bare_literal_adopts_the_dimension_it_is_combined_with(self) -> None:
        """The documented concession, applied uniformly.

        `wall + 2` means 2 mm, `max(bore, 8)` means 8 mm, and `plate >= 6`
        compares against 6 mm. It is one rule in one place rather than a list of
        special cases, and a literal can only ever adopt — never override.
        """
        values = {"wall": q(3, Unit.MM), "bore": q(12, Unit.MM)}
        assert evaluate("wall + 2", values).dimension == MM
        assert evaluate("max(bore, 8)", values).value == pytest.approx(12.0)
        assert evaluate("max(8, bore)", values).dimension == MM
        assert evaluate("wall >= 6", values).dimension == DIMENSIONLESS

    def test_two_real_units_that_disagree_are_still_refused(self) -> None:
        """The concession must not be able to reconcile a genuine mismatch."""
        values = {"wall": q(3, Unit.MM), "draft": q(2, Unit.DEG)}
        with pytest.raises(UnitError, match="wrong one"):
            evaluate("wall + draft", values)

    def test_min_and_max_do_not_depend_on_argument_order(self) -> None:
        values = {"bore": q(12, Unit.MM)}
        assert evaluate("max(bore, 8)", values).dimension == MM
        assert evaluate("max(8, bore)", values).dimension == MM

    def test_comparisons_and_conditions_are_dimensionless(self) -> None:
        values = {"t": q(8, Unit.MM)}
        assert evaluate("t >= 6", values).value == 1.0
        assert evaluate("t >= 6 and t <= 10", values).value == 1.0
        assert evaluate("12 if t >= 6 else 6", values).value == 12.0

    def test_both_branches_of_an_if_are_checked(self) -> None:
        """A unit error in the untaken branch is still an error.

        A design is written once and rebuilt many times; a latent mistake in the
        branch nobody took today surfaces during a rebuild six months later,
        which is the worst possible moment to discover it.
        """
        values = {"t": q(8, Unit.MM), "a": q(2, Unit.DEG)}
        with pytest.raises(UnitError, match="branches"):
            evaluate("t if t >= 6 else a", values)

    def test_a_chained_comparison_is_refused_rather_than_misread(self) -> None:
        with pytest.raises(ExpressionError, match="chain"):
            evaluate("1 < 2 < 3", {})

    def test_an_exponent_must_be_dimensionless_and_a_dimensioned_base_whole(self) -> None:
        values = {"r": q(3, Unit.MM), "a": q(2, Unit.DEG)}
        assert evaluate("r ** 2", values).dimension == UNIT_DIMENSIONS[Unit.MM2]
        with pytest.raises(UnitError, match="exponent"):
            evaluate("r ** a", values)
        with pytest.raises(UnitError, match="whole number"):
            evaluate("r ** 0.5", values)

    def test_division_by_zero_says_so_instead_of_raising_zerodivisionerror(self) -> None:
        with pytest.raises(ExpressionError, match="divides by zero"):
            evaluate("1 / 0", {})

    def test_an_unknown_name_lists_what_is_declared(self) -> None:
        with pytest.raises(ParameterError, match="wall"):
            evaluate("thickness * 2", {"wall": q(3, Unit.MM)})


class TestFunctions:
    def test_trig_reads_degrees_and_returns_a_plain_number(self) -> None:
        """Degrees are the project's angle unit; the conversion to radians
        happens inside the function and never to a value the design stores."""
        result = evaluate("sin(30)", {})
        assert result.value == pytest.approx(0.5)
        assert result.dimension == DIMENSIONLESS

    def test_inverse_trig_returns_degrees(self) -> None:
        result = evaluate("atan(1)", {})
        assert result.value == pytest.approx(45.0)
        assert result.dimension == DEG

    def test_atan2_needs_matching_operands_and_gives_degrees(self) -> None:
        values = {"rise": q(1, Unit.MM), "run": q(1, Unit.MM)}
        assert evaluate("atan2(rise, run)", values).value == pytest.approx(45.0)
        assert evaluate("atan2(rise, run)", values).dimension == DEG

    def test_sin_of_a_length_is_refused(self) -> None:
        with pytest.raises(UnitError, match="angle"):
            evaluate("sin(wall)", {"wall": q(3, Unit.MM)})

    def test_sqrt_halves_the_dimension(self) -> None:
        values = {"a": q(400, Unit.MM2)}
        result = evaluate("sqrt(a)", values)
        assert result.value == pytest.approx(20.0)
        assert result.dimension == MM

    def test_hypot_keeps_the_shared_dimension(self) -> None:
        values = {"x": q(3, Unit.MM), "y": q(4, Unit.MM)}
        result = evaluate("hypot(x, y)", values)
        assert result.value == pytest.approx(5.0)
        assert result.dimension == MM

    def test_step_rounds_up_to_a_stock_size(self) -> None:
        """Plate comes in half-millimetre steps; 6.3 mm of plate is 6.5 mm."""
        values = {"t": q(6.3, Unit.MM)}
        result = evaluate("step(t, 0.5)", values)
        assert result.value == pytest.approx(6.5)
        assert result.dimension == MM

    def test_step_refuses_a_non_positive_step(self) -> None:
        with pytest.raises(ExpressionError, match="positive"):
            evaluate("step(t, 0)", {"t": q(6.3, Unit.MM)})

    def test_log_and_exp_take_plain_numbers_only(self) -> None:
        assert evaluate("log(e)", {}).value == pytest.approx(1.0)
        with pytest.raises(UnitError, match="plain number"):
            evaluate("log(wall)", {"wall": q(3, Unit.MM)})

    def test_pi_is_available_and_dimensionless(self) -> None:
        assert evaluate("pi", {}).value == pytest.approx(math.pi)

    def test_the_wrong_number_of_arguments_reads_as_a_spec_error(self) -> None:
        with pytest.raises(ExpressionError, match="argument"):
            evaluate("sqrt(1, 2)", {})

    def test_keyword_arguments_are_refused(self) -> None:
        with pytest.raises(ExpressionError, match="positionally"):
            evaluate("round(1.5, digits=1)", {})


class TestTheWhitelistIsAWhitelist:
    """Expressions arrive from a model, through a document. They are not code."""

    @pytest.mark.parametrize(
        "source",
        [
            "__import__('os').system('echo hi')",
            "().__class__",
            "wall.value",
            "[x for x in (1, 2)]",
            "{'a': 1}",
            "lambda: 1",
            "open('x')",
            "wall[0]",
            "'text'",
            "f'{wall}'",
        ],
    )
    def test_anything_that_is_not_arithmetic_is_refused(self, source: str) -> None:
        with pytest.raises((ExpressionError, ParameterError)):
            evaluate(source, {"wall": q(3, Unit.MM)})

    def test_an_unknown_function_names_what_is_available(self) -> None:
        with pytest.raises(ExpressionError, match="Available"):
            evaluate("tanh(1)", {})

    def test_a_syntax_error_reads_as_a_spec_error(self) -> None:
        with pytest.raises(ExpressionError, match="not a valid expression"):
            evaluate("1 +", {})


class TestDependencies:
    def test_names_are_reported_sorted_and_deduplicated(self) -> None:
        """The graph's edges must not depend on where in a formula a name sat."""
        assert dependencies("b + a * b") == ("a", "b")

    def test_constants_and_functions_are_not_dependencies(self) -> None:
        assert dependencies("sqrt(a) * pi") == ("a",)

    def test_a_leading_equals_is_accepted(self) -> None:
        assert dependencies("= a + 1") == ("a",)


class TestParameterDeclaration:
    def test_a_parameter_is_either_a_decision_or_a_consequence(self) -> None:
        with pytest.raises(ParameterError, match="either a value or an expression"):
            Parameter("wall_mm", Unit.MM)
        with pytest.raises(ParameterError, match="either a value or an expression"):
            Parameter("wall_mm", Unit.MM, value=3.0, expression="1 + 1")

    def test_is_derived_distinguishes_the_two(self) -> None:
        assert not Parameter("a", Unit.MM, value=1.0).is_derived
        assert Parameter("b", Unit.MM, expression="1").is_derived

    @pytest.mark.parametrize("name", ["Wall", "wall mm", "wall.mm", "2wall", ""])
    def test_a_name_an_expression_could_not_refer_to_is_refused(self, name: str) -> None:
        with pytest.raises(ParameterError):
            Parameter(name, Unit.MM, value=1.0)

    @pytest.mark.parametrize("name", ["min", "max", "sqrt", "pi", "step"])
    def test_a_parameter_cannot_shadow_a_builtin(self, name: str) -> None:
        with pytest.raises(ParameterError, match="built-in"):
            Parameter(name, Unit.MM, value=1.0)

    def test_a_duplicate_declaration_is_refused(self) -> None:
        with pytest.raises(ParameterError, match="declared twice"):
            ParameterSet.of(
                [Parameter("a", Unit.MM, value=1.0), Parameter("a", Unit.MM, value=2.0)]
            )


class TestResolution:
    def _press(self) -> ParameterSet:
        """A small real chain: blank thickness decides clearance decides force."""
        return ParameterSet.of(
            [
                Parameter("blank_t_mm", Unit.MM, value=2.0),
                Parameter("perimeter_mm", Unit.MM, value=800.0),
                Parameter("shear_mpa", Unit.MPA, value=350.0),
                Parameter("clearance_mm", Unit.MM, expression="blank_t_mm * 0.06"),
                Parameter(
                    "shear_force_n",
                    Unit.NEWTON,
                    expression="perimeter_mm * blank_t_mm * shear_mpa",
                ),
            ]
        )

    def test_a_chain_resolves_in_dependency_order(self) -> None:
        resolved = self._press().resolve()
        assert resolved.order.index("blank_t_mm") < resolved.order.index("clearance_mm")
        assert resolved.number("clearance_mm") == pytest.approx(0.12)
        assert resolved.number("shear_force_n") == pytest.approx(560_000.0)

    def test_a_derived_value_keeps_its_declared_dimension(self) -> None:
        resolved = self._press().resolve()
        assert resolved["shear_force_n"].dimension == NEWTON

    def test_resolution_is_deterministic(self) -> None:
        """Same input, same order — the guarantee roadmap I5 rests on."""
        first = self._press().resolve()
        second = self._press().resolve()
        assert first.order == second.order
        assert first.as_numbers() == second.as_numbers()

    def test_a_declared_unit_that_the_formula_contradicts_is_refused(self) -> None:
        """This is the error the whole module exists for: a plausible number."""
        parameters = ParameterSet.of(
            [
                Parameter("draft_deg", Unit.DEG, value=3.0),
                Parameter("pad_mm", Unit.MM, expression="draft_deg * 2"),
            ]
        )
        with pytest.raises(UnitError, match="declared in mm"):
            parameters.resolve()

    def test_an_undeclared_dependency_lists_what_is_declared(self) -> None:
        parameters = ParameterSet.of(
            [Parameter("a", Unit.MM, value=1.0), Parameter("b", Unit.MM, expression="c + 1")]
        )
        with pytest.raises(ParameterError, match="not declared"):
            parameters.resolve()

    def test_declaration_order_does_not_have_to_be_dependency_order(self) -> None:
        parameters = ParameterSet.of(
            [
                Parameter("derived", Unit.MM, expression="base * 2"),
                Parameter("base", Unit.MM, value=4.0),
            ]
        )
        assert parameters.resolve().number("derived") == pytest.approx(8.0)


class TestCycles:
    def test_a_self_reference_is_named(self) -> None:
        parameters = ParameterSet.of([Parameter("a", Unit.MM, expression="a + 1")])
        with pytest.raises(CycleError, match="in terms of itself"):
            parameters.resolve()

    def test_a_loop_is_reported_as_a_path(self) -> None:
        """Knowing *which* loop is the difference between a fix and a search."""
        parameters = ParameterSet.of(
            [
                Parameter("a", Unit.MM, expression="b + 1"),
                Parameter("b", Unit.MM, expression="c + 1"),
                Parameter("c", Unit.MM, expression="a + 1"),
            ]
        )
        with pytest.raises(CycleError) as caught:
            parameters.resolve()
        message = str(caught.value)
        assert "->" in message
        for name in ("a", "b", "c"):
            assert name in message

    def test_a_diamond_is_not_a_cycle(self) -> None:
        """Two paths to one parameter is ordinary, and must not be misreported."""
        parameters = ParameterSet.of(
            [
                Parameter("base", Unit.MM, value=10.0),
                Parameter("left", Unit.MM, expression="base * 2"),
                Parameter("right", Unit.MM, expression="base * 3"),
                Parameter("top", Unit.MM, expression="left + right"),
            ]
        )
        assert parameters.resolve().number("top") == pytest.approx(50.0)


class TestImpact:
    """The parameter half of B4: what moves when one number changes."""

    def test_dependents_are_found_transitively(self) -> None:
        parameters = ParameterSet.of(
            [
                Parameter("base", Unit.MM, value=10.0),
                Parameter("mid", Unit.MM, expression="base * 2"),
                Parameter("top", Unit.MM, expression="mid + 1"),
                Parameter("elsewhere", Unit.MM, value=5.0),
            ]
        )
        resolved = parameters.resolve()
        assert resolved.dependents_of("base") == ("mid", "top")
        assert resolved.dependents_of("elsewhere") == ()

    def test_asking_for_an_unknown_parameter_lists_the_known_ones(self) -> None:
        resolved = ParameterSet.of([Parameter("a", Unit.MM, value=1.0)]).resolve()
        with pytest.raises(ParameterError, match="Declared"):
            resolved["nope"]


class TestOperatorsInFull:
    """The branches an expression reaches only when someone writes them.

    Each of these is a line in the evaluator that a design will eventually hit —
    a negated dimension, a remainder, a boolean literal — and an evaluator that
    is wrong on one of them is wrong quietly, producing a number.
    """

    def test_negation_keeps_the_dimension(self) -> None:
        assert evaluate("-wall", {"wall": q(3, Unit.MM)}).value == pytest.approx(-3.0)
        assert evaluate("-wall", {"wall": q(3, Unit.MM)}).dimension == MM

    def test_a_unary_plus_changes_nothing(self) -> None:
        assert evaluate("+wall", {"wall": q(3, Unit.MM)}).dimension == MM

    def test_subtraction_carries_the_dimension(self) -> None:
        values = {"a": q(10, Unit.MM), "b": q(4, Unit.MM)}
        result = evaluate("a - b", values)
        assert result.value == pytest.approx(6.0)
        assert result.dimension == MM

    def test_a_remainder_keeps_the_dimension(self) -> None:
        """`length % pitch` is how a bolt circle checks it divides evenly."""
        values = {"length": q(100, Unit.MM), "pitch": q(30, Unit.MM)}
        result = evaluate("length % pitch", values)
        assert result.value == pytest.approx(10.0)
        assert result.dimension == MM

    def test_a_remainder_modulo_zero_says_so(self) -> None:
        with pytest.raises(ExpressionError, match="modulo zero"):
            evaluate("10 % 0", {})

    def test_not_inverts_a_condition(self) -> None:
        assert evaluate("not (1 > 2)", {}).value == 1.0

    def test_not_on_a_dimensioned_value_is_refused(self) -> None:
        with pytest.raises(UnitError, match="takes a condition"):
            evaluate("not wall", {"wall": q(3, Unit.MM)})

    def test_boolean_literals_are_numbers(self) -> None:
        assert evaluate("True", {}).value == 1.0
        assert evaluate("False", {}).value == 0.0

    def test_and_or_refuse_a_dimensioned_operand(self) -> None:
        with pytest.raises(UnitError, match="Compare it against"):
            evaluate("wall and True", {"wall": q(3, Unit.MM)})

    def test_an_if_condition_must_be_a_condition(self) -> None:
        with pytest.raises(UnitError, match="must be a condition"):
            evaluate("1 if wall else 2", {"wall": q(3, Unit.MM)})

    def test_a_negative_base_under_a_fractional_power_has_no_real_answer(self) -> None:
        """Python answers this with a complex number, which is not a dimension
        this design can carry — so it is refused rather than propagated."""
        with pytest.raises(ExpressionError, match="no real answer"):
            evaluate("(0 - 8) ** 0.5", {})

    def test_a_dimensionless_base_takes_a_fractional_power(self) -> None:
        assert evaluate("9 ** 0.5", {}).value == pytest.approx(3.0)

    def test_round_takes_a_digit_count(self) -> None:
        values = {"x": q(6.34, Unit.MM)}
        assert evaluate("round(x, 1)", values).value == pytest.approx(6.3)
        assert evaluate("round(x, 1)", values).dimension == MM
        assert evaluate("round(x)", values).value == pytest.approx(6.0)

    def test_rounds_second_argument_must_be_a_plain_number(self) -> None:
        with pytest.raises(UnitError, match="digit count"):
            evaluate("round(x, y)", {"x": q(6.34, Unit.MM), "y": q(1, Unit.MM)})

    def test_an_unsupported_binary_operator_is_refused(self) -> None:
        with pytest.raises(ExpressionError, match="not allowed"):
            evaluate("1 // 2", {})

    def test_an_empty_expression_is_refused(self) -> None:
        with pytest.raises(ExpressionError, match="cannot be empty"):
            evaluate("=", {})

    def test_an_unsupported_comparison_is_refused(self) -> None:
        with pytest.raises((ExpressionError, ParameterError)):
            evaluate("1 in (1, 2)", {})


class TestQuantityAndSetHelpers:
    def test_a_quantity_knows_its_unit_when_the_dimension_has_a_name(self) -> None:
        assert q(3, Unit.MM).unit is Unit.MM
        assert Quantity(3.0, MM * NEWTON).unit is None

    def test_a_parameter_set_reports_its_length_and_iterates_in_order(self) -> None:
        parameters = ParameterSet.of(
            [Parameter("a", Unit.MM, value=1.0), Parameter("b", Unit.MM, value=2.0)]
        )
        assert len(parameters) == 2
        assert [p.name for p in parameters] == ["a", "b"]
        assert parameters.names() == ("a", "b")

    def test_inverse_trig_refuses_a_dimensioned_argument(self) -> None:
        """`asin(wall_mm)` is a mistake every time — the argument is a ratio."""
        with pytest.raises(UnitError, match="plain number"):
            evaluate("asin(wall)", {"wall": q(0.5, Unit.MM)})
