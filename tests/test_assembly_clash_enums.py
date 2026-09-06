"""What a DMU clash run is actually asked for, pinned to what the seat answered.

Measured on ladder prompt S2, 2026-09-06, on a real V5-R33. Two defects stacked
in one function, and the first hid the second:

* `clash.ComputedResults` does not exist. The seat answered `AttributeError:
  Add.ComputedResults`, the agent read that as an FEA prerequisite, invented a
  story about needing computed results from a structural analysis, and offered
  to run one. The member is `Conflicts`.

* `_CLASH_TYPES` read `{"contact": 1, "clash": 2, "clearance": 3}` and every row
  was wrong, because `ComputationType` is the **scope** of the run and not what
  it looks for. Probed against two 40x40x30 blocks overlapping by 20 mm:

      ComputationType=1  ->  1 conflict, value -20.0358
      ComputationType=2  ->  0 conflicts
      ComputationType=3  ->  0 conflicts

  So the word an engineer is most likely to use, `clash`, selected the value
  that finds nothing. The check would have reported a clean assembly on two
  solids sharing 24 000 mm3.

* `Conflict.Status` was 0 for every conflict measured -- overlapping, touching,
  and a clearance hit alike -- so it cannot be the discriminator the old
  `_CLASH_RESULTS` made it. Worse, it mapped 0 to "no_interference" and skipped
  on that word, so even with the collection found, every real conflict would
  have been dropped and the assembly called clear. The sign of `Value` is what
  discriminates:

      overlap 20 mm    value -20.0358   (negative: penetration)
      touching         value   0.0000
      gap 3 mm, clearance 5 mm, InterferenceType=1   value 3.0000

  The same 20 mm overlap read -20.0358 on one run and -19.6484 on another, so
  the magnitude is tessellation-dependent and the payload says so.

The COM calls are Windows-only. What is pinned here is every decision taken
before one is made, plus the classification of what comes back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.com import assembly_review  # noqa: E402


class TestTheScopeAndInterferenceValues:
    """The two properties the old single table conflated."""

    def test_the_scope_is_between_all_components(self) -> None:
        # 1 is the only ComputationType that reported anything without
        # FirstGroup/SecondGroup being set.
        assert assembly_review._SCOPE_BETWEEN_ALL == 1

    def test_contact_and_clearance_are_interference_types_not_scopes(self) -> None:
        assert assembly_review._INTERFERENCE_CONTACT == 0
        assert assembly_review._INTERFERENCE_CLEARANCE == 1

    def test_the_wrong_table_is_gone(self) -> None:
        """The specific regression: a name that maps `clash` to 2 is the bug."""
        assert not hasattr(assembly_review, "_CLASH_TYPES")
        assert not hasattr(assembly_review, "_CLASH_RESULTS")


class TestAConflictIsClassifiedByTheSignOfItsValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (-20.0358, "clash"),  # the measured 20 mm overlap
            (-19.6484, "clash"),  # the same overlap, remeasured
            (-1e-3, "clash"),
            (0.0, "contact"),  # the measured touching case
            (3.0, "clearance"),  # the measured 3 mm gap at clearance 5
            (0.5, "clearance"),
        ],
    )
    def test_measured_values(self, value: float, expected: str) -> None:
        assert assembly_review._classify(value) == expected

    def test_a_value_that_could_not_be_read_is_not_a_pass(self) -> None:
        """`_number` returns None when the property is absent. Calling that
        "no interference" is the failure this whole module exists to stop."""
        assert assembly_review._classify(None) == "unknown"

    def test_zero_is_contact_not_clearance(self) -> None:
        """Touching is a problem for a fit; reporting it as clearance hides it."""
        assert assembly_review._classify(0.0) == "contact"
        assert assembly_review._classify(assembly_review._TOUCHING_MM) == "contact"


class TestTheHonestDenominator:
    """Zero conflicts over zero components proves nothing."""

    def test_a_product_with_components_reports_them(self) -> None:
        product = type("P", (), {"Products": type("C", (), {"Count": 2})()})()
        assert assembly_review._component_count(product) == 2

    def test_something_that_is_not_a_product_reports_zero(self) -> None:
        assert assembly_review._component_count(object()) == 0

    def test_the_payload_no_longer_calls_conflicts_pairs_checked(self) -> None:
        """`Conflicts.Count` is the number of problems, never the number of
        pairs examined -- so a clean assembly used to report that nothing had
        been looked at."""
        source = Path(assembly_review.__file__).read_text(encoding="utf-8")
        assert "pairs_checked" not in source
        assert "components_in_assembly" in source


class TestTheApproximationIsDeclared:
    def test_every_conflict_is_marked_approximate(self) -> None:
        source = Path(assembly_review.__file__).read_text(encoding="utf-8")
        assert '"value_is_approximate": True' in source

    def test_the_payload_says_why(self) -> None:
        source = Path(assembly_review.__file__).read_text(encoding="utf-8")
        assert "tessellation" in source


class _Stub:
    """Enough of a context for a refusal decided before any COM call.

    `_document()` raises, so a test that reaches it has proved the refusal did
    not happen first -- which is the whole claim.
    """

    def _document(self):  # noqa: ANN202 - a stub, deliberately
        raise AssertionError("a bad argument must be refused before CATIA is touched")


class TestAClearanceRunWithoutADistanceIsRefused:
    """Asked for a clearance check with no distance, the old code set
    `Clearance` to nothing and ran a contact check under a clearance label --
    an answer to a question nobody asked, reported as the answer to the one
    they did."""

    def test_no_clearance_at_all(self) -> None:
        with pytest.raises(CatiaOperationError, match="needs a distance"):
            assembly_review.AssemblyReviewMixin.assembly_clash(
                _Stub(), kind="clearance"
            )

    def test_a_zero_clearance(self) -> None:
        with pytest.raises(CatiaOperationError, match="clearance_mm"):
            assembly_review.AssemblyReviewMixin.assembly_clash(
                _Stub(), kind="clearance", clearance_mm=0
            )

    def test_the_refusal_names_the_alternative(self) -> None:
        with pytest.raises(CatiaOperationError, match="kind='clash'"):
            assembly_review.AssemblyReviewMixin.assembly_clash(
                _Stub(), kind="clearance"
            )

    def test_a_clash_run_needs_no_clearance_and_is_not_refused_here(self) -> None:
        """The guard must not refuse the ordinary case: this reaches COM, which
        the stub marks by raising its own assertion."""
        with pytest.raises(AssertionError, match="before CATIA is touched"):
            assembly_review.AssemblyReviewMixin.assembly_clash(_Stub(), kind="clash")
