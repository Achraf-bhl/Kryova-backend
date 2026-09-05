"""Machine-checkable claims about a built part.

The tests are named after what would go wrong without them.

* **An unmeasured claim is never a pass.** A suite that silently skips what it
  could not read reports green on a part nobody checked. This is the reason the
  module exists in the shape it does, so it is the first thing tested.
* **A bound may be a formula**, so an assertion travels with the design instead
  of being a number that quietly stops matching it.
* **An approximate number is reported as approximate.** The mock says so about
  its own mass; a report that laundered that into a fact would defeat the
  warning one layer up.

Offline: no database fixture, no CATIA, no gmsh.
"""

import pytest

from app.design.assertions import (
    Assertion,
    AssertionReport,
    Outcome,
    check_assertions,
    measurable_paths,
    read_measurement,
)
from app.design.errors import SpecError
from app.design.params import Parameter, ParameterSet, Unit

MEASURED = {
    "mass_kg": 4.5,
    "volume_mm3": 573248.0,
    "bounding_box_mm": {"size": [120.0, 80.0, 8.0], "min": [0.0, 0.0, 0.0]},
    "center_of_gravity_mm": [60.0, 40.0, 4.0],
    "has_solid": True,
    "material": "steel-1018",
}


def resolved(**values: float) -> object:
    """A tiny resolved parameter set, for bounds written as formulas."""
    return ParameterSet.of(
        Parameter(name, Unit.NONE, value=value) for name, value in values.items()
    ).resolve()


class TestAnUnmeasuredClaimIsNotAPass:
    def test_a_missing_measurement_is_its_own_outcome(self) -> None:
        """Not a pass, and not a failure — the recoveries differ.

        A failure means change the design. This means go and measure it.
        """
        report = check_assertions(
            [Assertion("wall", "min_wall_mm", ">=", 3.0)], MEASURED
        )

        assert len(report.unmeasured) == 1
        assert report.results[0].outcome is Outcome.UNMEASURED
        assert not report.failed

    def test_a_report_containing_one_is_not_ok(self) -> None:
        report = check_assertions(
            [
                Assertion("mass", "mass_kg", "<=", 10.0),
                Assertion("wall", "min_wall_mm", ">=", 3.0),
            ],
            MEASURED,
        )

        assert report.passed, "the measurable one should still have passed"
        assert not report.ok, "but the part has not been verified"
        assert not report

    def test_the_message_says_what_was_measurable_instead(self) -> None:
        """"Not found" is a shrug; a list of what *is* there is a next step."""
        report = check_assertions(
            [Assertion("wall", "min_wall_mm", ">=", 3.0)], MEASURED
        )

        reason = report.results[0].reason
        assert "min_wall_mm" in reason
        assert "mass_kg" in reason
        assert "catia_measure" in reason

    def test_a_non_numeric_value_is_unmeasured_not_a_crash(self) -> None:
        report = check_assertions(
            [Assertion("material", "material", "==", 1.0, tolerance=0.1)], MEASURED
        )

        assert report.results[0].outcome is Outcome.UNMEASURED

    def test_an_empty_suite_is_not_a_pass(self) -> None:
        """Nothing was claimed, so nothing was verified."""
        assert not check_assertions([], MEASURED).ok


class TestComparisons:
    @pytest.mark.parametrize(
        ("comparison", "bound", "expected"),
        [
            ("<=", 5.0, True),
            ("<=", 4.0, False),
            ("<=", 4.5, True),
            (">=", 4.0, True),
            (">=", 5.0, False),
            ("<", 5.0, True),
            ("<", 4.5, False),
            (">", 4.0, True),
            (">", 4.5, False),
            ("!=", 9.0, True),
            ("!=", 4.5, False),
        ],
    )
    def test_each_comparison_does_what_it_says(
        self, comparison: str, bound: float, expected: bool
    ) -> None:
        report = check_assertions(
            [Assertion("m", "mass_kg", comparison, bound)], MEASURED
        )
        assert report.results[0].passed is expected

    def test_tolerance_widens_what_is_acceptable(self) -> None:
        """Slack means "we cannot measure finer than this", never "worse will do"."""
        strict = check_assertions([Assertion("m", "mass_kg", "<=", 4.4)], MEASURED)
        slack = check_assertions(
            [Assertion("m", "mass_kg", "<=", 4.4, tolerance=0.2)], MEASURED
        )

        assert not strict.ok
        assert slack.ok

    def test_an_exact_equality_without_tolerance_is_refused(self) -> None:
        """A kernel does not return round decimals; that assertion always fails."""
        with pytest.raises(SpecError, match="tolerance"):
            Assertion("thickness", "bounding_box_mm.size[2]", "==", 8.0)

    def test_equality_with_tolerance_is_allowed_and_works(self) -> None:
        report = check_assertions(
            [Assertion("t", "bounding_box_mm.size[2]", "==", 8.0, tolerance=0.01)],
            MEASURED,
        )
        assert report.ok


class TestABoundCanBeAFormula:
    def test_a_formula_is_evaluated_against_the_designs_parameters(self) -> None:
        """This is what lets an assertion travel with a parametric design."""
        report = check_assertions(
            [Assertion("mass", "mass_kg", "<=", "=target_mass_kg")],
            MEASURED,
            parameters=resolved(target_mass_kg=5.0),
        )

        assert report.ok
        assert report.results[0].expected == 5.0

    def test_the_same_assertion_moves_when_the_parameter_does(self) -> None:
        assertion = Assertion("mass", "mass_kg", "<=", "=target_mass_kg")

        generous = check_assertions([assertion], MEASURED, parameters=resolved(target_mass_kg=5.0))
        tight = check_assertions([assertion], MEASURED, parameters=resolved(target_mass_kg=4.0))

        assert generous.ok
        assert not tight.ok

    def test_a_formula_with_no_parameters_supplied_is_unmeasured(self) -> None:
        report = check_assertions(
            [Assertion("mass", "mass_kg", "<=", "=target_mass_kg")], MEASURED
        )

        assert report.results[0].outcome is Outcome.UNMEASURED
        assert "parameters" in report.results[0].reason

    def test_a_bare_string_bound_is_refused_as_neither(self) -> None:
        report = check_assertions(
            [Assertion("mass", "mass_kg", "<=", "target_mass_kg")],
            MEASURED,
            parameters=resolved(target_mass_kg=5.0),
        )

        assert report.results[0].outcome is Outcome.UNMEASURED
        assert "'=' " in report.results[0].reason or "starts with '='" in (
            report.results[0].reason
        )


class TestTheFailureIsActionable:
    def test_a_failure_carries_the_size_of_the_gap(self) -> None:
        """"0.5 over" is actionable; "failed" is a retry counter."""
        report = check_assertions([Assertion("m", "mass_kg", "<=", 4.0)], MEASURED)

        result = report.results[0]
        assert result.outcome is Outcome.FAILED
        assert result.measured == 4.5
        assert result.expected == 4.0
        assert result.gap == pytest.approx(0.5)

    def test_a_passing_assertion_has_no_gap(self) -> None:
        report = check_assertions([Assertion("m", "mass_kg", "<=", 9.0)], MEASURED)
        assert report.results[0].gap is None

    def test_the_note_is_repeated_in_the_failure(self) -> None:
        """A number with a reason attached is arguable; one without is not."""
        report = check_assertions(
            [Assertion("m", "mass_kg", "<=", 4.0, note="Weight budget from the spec.")],
            MEASURED,
        )

        assert "Weight budget from the spec." in str(report.results[0])

    def test_every_assertion_is_checked_not_just_the_first(self) -> None:
        """Three failing together is one cause; stopping at the first hides that."""
        report = check_assertions(
            [
                Assertion("a", "mass_kg", "<=", 1.0),
                Assertion("b", "volume_mm3", "<=", 1.0),
                Assertion("c", "mass_kg", ">=", 99.0),
            ],
            MEASURED,
        )

        assert len(report.failed) == 3


class TestApproximateNumbers:
    def test_an_approximate_measurement_is_flagged_through_the_report(self) -> None:
        """The mock warns about its own mass; that warning must survive."""
        report = check_assertions(
            [Assertion("m", "mass_kg", "<=", 9.0)], {**MEASURED, "approximate": True}
        )

        assert report.ok
        assert report.approximate
        assert "approximate" in report.summary()

    def test_a_real_measurement_is_not_flagged(self) -> None:
        report = check_assertions([Assertion("m", "mass_kg", "<=", 9.0)], MEASURED)

        assert not report.approximate
        assert "approximate" not in report.summary()


class TestReadingAPayload:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("mass_kg", 4.5),
            ("bounding_box_mm.size[2]", 8.0),
            ("center_of_gravity_mm[0]", 60.0),
            ("bounding_box_mm.min[1]", 0.0),
        ],
    )
    def test_paths_reach_nested_numbers(self, path: str, expected: float) -> None:
        assert read_measurement(MEASURED, path) == expected

    @pytest.mark.parametrize(
        "path",
        [
            "nope",
            "bounding_box_mm.nope",
            "bounding_box_mm.size[9]",
            "mass_kg.deeper",
            "has_solid",  # a bool is not a measurement
            "material",
        ],
    )
    def test_a_path_that_does_not_reach_a_number_returns_nothing(self, path: str) -> None:
        assert read_measurement(MEASURED, path) is None

    def test_measurable_paths_lists_what_can_be_checked(self) -> None:
        paths = measurable_paths(MEASURED)

        assert "mass_kg" in paths
        assert "bounding_box_mm.size[2]" in paths
        assert "has_solid" not in paths, "a boolean is not a measurement"
        assert "material" not in paths


class TestTheAssertionItself:
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"name": "", "measure": "mass_kg", "comparison": "<=", "bound": 1.0}, "needs a name"),
            ({"name": "a", "measure": "", "comparison": "<=", "bound": 1.0}, "measure"),
            ({"name": "a", "measure": "m", "comparison": "~=", "bound": 1.0}, "comparison"),
            (
                {"name": "a", "measure": "m", "comparison": "<=", "bound": 1.0, "tolerance": -1.0},
                "negative tolerance",
            ),
        ],
    )
    def test_a_malformed_assertion_is_refused_where_it_is_written(
        self, kwargs: dict[str, object], match: str
    ) -> None:
        with pytest.raises(SpecError, match=match):
            Assertion(**kwargs)  # type: ignore[arg-type]

    def test_it_round_trips_through_a_dict(self) -> None:
        original = Assertion("m", "mass_kg", "<=", 4.0, tolerance=0.1, note="why")

        assert Assertion.from_dict(original.to_dict()) == original

    def test_an_unknown_key_is_refused_rather_than_dropped(self) -> None:
        with pytest.raises(SpecError, match="unknown keys"):
            Assertion.from_dict({"name": "a", "measure": "m", "comparison": "<=",
                                 "bound": 1.0, "tolerence": 0.1})

    def test_the_report_serialises(self) -> None:
        report = check_assertions([Assertion("m", "mass_kg", "<=", 4.0)], MEASURED)
        data = report.to_dict()

        assert data["ok"] is False
        assert data["failed"] == 1
        assert data["results"][0]["gap"] == pytest.approx(0.5)

    def test_an_empty_report_says_so(self) -> None:
        assert "No assertions" in AssertionReport().summary()
