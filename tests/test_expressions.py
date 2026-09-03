"""Formula evaluation, and everything it refuses to evaluate.

The refusals matter more than the arithmetic here. These expressions come from a
language model, one prompt injection away from being attacker-written, and they
are evaluated inside the process holding a COM handle to the engineer's CATIA.
`eval` on such a string is remote code execution on their workstation; this is
the parser that exists instead, and these are the checks that it stays closed.
"""

from __future__ import annotations

import math

import pytest

from scripts.catia_bridge.expressions import (
    ExpressionError,
    evaluate,
    parameter_names,
)

PARAMETERS = {"Width": 40.0, "Height": 20.0, "Clearance": 0.5, "Count": 4.0}


class TestArithmetic:
    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("Width * 2", 80.0),
            ("Width + Clearance", 40.5),
            ("Width - Height", 20.0),
            ("Width / Count", 10.0),
            ("Width % 30", 10.0),
            ("2 ** 3", 8.0),
            ("-Width", -40.0),
            ("(Width + Height) / 2", 30.0),
            ("Width * 2 + Height * 3", 140.0),
        ],
    )
    def test_evaluates_arithmetic_over_parameters(
        self, expression: str, expected: float
    ) -> None:
        assert evaluate(expression, PARAMETERS) == pytest.approx(expected)

    def test_operator_precedence_is_pythons_not_left_to_right(self) -> None:
        # Worth pinning: a hand-rolled left-to-right evaluator would give 180
        # here, and a wrong bracket in a dimension formula is a wrong part.
        assert evaluate("Width + Height * 2", PARAMETERS) == pytest.approx(80.0)


class TestUnits:
    def test_inline_millimetres_are_stripped(self) -> None:
        # CATIA writes units into the expression itself; everything in this
        # system is already millimetres, so they carry no information.
        assert evaluate("10mm + 5mm", PARAMETERS) == pytest.approx(15.0)

    def test_a_unit_with_a_space_before_it_is_stripped_too(self) -> None:
        assert evaluate("10 mm * 2", PARAMETERS) == pytest.approx(20.0)

    def test_degrees_are_recognised_as_a_unit(self) -> None:
        assert evaluate("45deg + 45deg", PARAMETERS) == pytest.approx(90.0)


class TestFunctions:
    def test_the_maths_functions_a_dimension_actually_uses(self) -> None:
        assert evaluate("sqrt(Width * Height)", PARAMETERS) == pytest.approx(
            math.sqrt(800.0)
        )
        assert evaluate("max(Width, Height)", PARAMETERS) == pytest.approx(40.0)
        assert evaluate("round(Clearance)", PARAMETERS) == pytest.approx(0.0)

    def test_trigonometry_is_in_degrees_like_the_rest_of_the_system(self) -> None:
        # Radians here would be a silent factor-of-57 error in any formula that
        # positions a hole on a bolt circle.
        assert evaluate("sin(90)", PARAMETERS) == pytest.approx(1.0)
        assert evaluate("cos(0)", PARAMETERS) == pytest.approx(1.0)

    def test_an_unknown_function_is_refused_by_name(self) -> None:
        with pytest.raises(ExpressionError, match="not a function"):
            evaluate("exec('x')", PARAMETERS)


class TestRefusals:
    """Everything that is not arithmetic over parameters."""

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('rm -rf /')",
            "().__class__.__bases__",
            "open('/etc/passwd').read()",
            "[x for x in range(10)]",
            "lambda: 1",
            "{'a': 1}",
            "Width if Width else Height",
            "globals()",
        ],
    )
    def test_anything_that_is_not_arithmetic_is_refused(self, expression: str) -> None:
        # The parametrisation is the test: each of these is a distinct node type
        # that a default-allow evaluator would have executed.
        with pytest.raises(ExpressionError):
            evaluate(expression, PARAMETERS)

    def test_attribute_access_is_refused_even_on_a_real_parameter(self) -> None:
        # `Width.__class__` is the first step of every sandbox escape there is.
        with pytest.raises(ExpressionError):
            evaluate("Width.__class__", PARAMETERS)

    def test_an_unknown_parameter_is_named_along_with_what_does_exist(self) -> None:
        with pytest.raises(ExpressionError, match="Thickness"):
            evaluate("Thickness * 2", PARAMETERS)

    def test_a_syntax_error_says_so_rather_than_crashing(self) -> None:
        with pytest.raises(ExpressionError, match="not a valid expression"):
            evaluate("Width * * 2", PARAMETERS)

    def test_division_by_zero_is_reported_not_raised_as_arithmetic(self) -> None:
        with pytest.raises(ExpressionError, match="divides by zero"):
            evaluate("Width / 0", PARAMETERS)

    def test_a_huge_exponent_cannot_be_used_to_hang_the_bridge(self) -> None:
        # `2 ** 10 ** 10` is three tokens and would occupy the process for a
        # very long time. The bridge is single-threaded per device.
        with pytest.raises(ExpressionError, match="too large"):
            evaluate("2 ** 10 ** 10", PARAMETERS)

    def test_a_string_constant_is_not_a_dimension(self) -> None:
        with pytest.raises(ExpressionError, match="not a number"):
            evaluate("'40'", PARAMETERS)


class TestComparisons:
    """Used by checks, which assert a condition rather than compute a value."""

    def test_a_satisfied_condition_is_true(self) -> None:
        assert evaluate("Width > Height", PARAMETERS) == pytest.approx(1.0)

    def test_a_violated_condition_is_false(self) -> None:
        assert evaluate("Clearance > 1", PARAMETERS) == pytest.approx(0.0)

    def test_conditions_combine(self) -> None:
        assert evaluate("Width > 10 and Height > 10", PARAMETERS) == pytest.approx(1.0)


class TestParameterNames:
    def test_reports_what_a_formula_reads(self) -> None:
        # Checked before a formula is stored, so a typo names the parameter
        # rather than turning up later as a value that stopped updating.
        assert parameter_names("Width * 2 + Clearance") == ["Width", "Clearance"]

    def test_does_not_report_functions_as_parameters(self) -> None:
        assert parameter_names("sqrt(Width)") == ["Width"]

    def test_reports_each_name_once(self) -> None:
        assert parameter_names("Width + Width") == ["Width"]

    def test_a_formula_with_no_parameters_reads_nothing(self) -> None:
        assert parameter_names("10 * 2") == []
