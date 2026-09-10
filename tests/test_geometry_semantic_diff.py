"""Semantic diffs on CAD, under a tolerance policy (E15 task 3).

The phase's own reasoning is that line diffs on CAD are meaningless and
"reviewers should see only consequential change". Both halves of that are
testable and both can go wrong in opposite directions — a tolerance too tight
buries a reviewer in re-export noise until they stop reading diffs, and one too
loose hides an edit somebody made on purpose. So the tests are mostly about
where the line sits and what is deliberately never on the tolerant side of it.
"""

from __future__ import annotations

from app.geometry.semantic_diff import RELATIVE_TOLERANCE, compare

BRACKET = {
    "volume_mm3": 96_000.0,
    "surface_area_mm2": 27_200.0,
    "mass_kg": 0.2592,
    "solid_count": 1,
    "face_count": 40,
    "edge_count": 96,
    "vertex_count": 64,
    "bounding_box_min_mm": [0.0, 0.0, 0.0],
    "bounding_box_max_mm": [120.0, 80.0, 10.0],
    "centre_of_mass_mm": [60.0, 40.0, 5.0],
}


def moved(field: str, factor: float) -> dict:
    return {**BRACKET, field: BRACKET[field] * factor}


class TestIdenticalGeometry:
    def test_the_same_bytes_stop_the_comparison(self) -> None:
        """The content-addressed store already answers "is this the same file".
        Re-deriving it from measurements would be slower and less certain."""
        result = compare({}, {}, before_sha256="a" * 64, after_sha256="a" * 64)

        assert result.identical_bytes
        assert not result
        assert "same bytes" in result.summary()

    def test_no_change_is_a_positive_answer_rather_than_an_empty_one(self) -> None:
        """A reviewer told "no differences" learns the re-export was clean; one
        shown a blank panel assumes it is broken."""
        result = compare(BRACKET, dict(BRACKET))

        assert not result
        assert "No consequential change" in result.summary()
        assert result.complete

    def test_re_export_noise_is_below_the_tolerance(self) -> None:
        """The case the whole module exists for: the same part re-exported, with
        floating-point tails that differ and geometry that does not."""
        noisy = {
            **BRACKET,
            "volume_mm3": 96_000.0 * (1 + RELATIVE_TOLERANCE / 10),
            "surface_area_mm2": 27_200.0 * (1 - RELATIVE_TOLERANCE / 10),
        }

        assert not compare(BRACKET, noisy)


class TestTheTolerance:
    def test_a_change_just_over_the_tolerance_is_reported(self) -> None:
        result = compare(BRACKET, moved("volume_mm3", 1 + RELATIVE_TOLERANCE * 2))

        assert result
        assert [change.name for change in result.changes] == ["volume_mm3"]

    def test_a_change_just_under_the_tolerance_is_not(self) -> None:
        assert not compare(BRACKET, moved("volume_mm3", 1 + RELATIVE_TOLERANCE / 2))

    def test_the_measure_is_symmetric_between_growing_and_shrinking(self) -> None:
        """A part that doubled and one that halved have moved by the same amount.
        Dividing by `before` makes them 100% and 50%, which puts one edit on
        each side of the tolerance."""
        grew = compare(BRACKET, moved("volume_mm3", 2.0))
        shrank = compare(moved("volume_mm3", 2.0), BRACKET)

        assert grew.changes[0].relative == shrank.changes[0].relative

    def test_a_quantity_that_went_to_zero_is_a_change_not_an_infinity(self) -> None:
        result = compare(BRACKET, {**BRACKET, "volume_mm3": 0.0})

        assert result
        assert result.changes[0].relative == 1.0

    def test_a_hairline_change_from_zero_is_not_an_unbounded_one(self) -> None:
        """Without the absolute floor, a version that gained a sliver reports as
        an infinite change and every panel shows it as the headline."""
        result = compare(
            {**BRACKET, "volume_mm3": 0.0}, {**BRACKET, "volume_mm3": 1e-12}
        )

        assert not result


class TestCounts:
    def test_a_gained_face_is_always_a_change(self) -> None:
        """There is no sense in which 41 faces is 40 faces to within a tolerance,
        and smoothing it over hides the most reviewable change there is."""
        result = compare(BRACKET, {**BRACKET, "face_count": 41})

        assert result
        change = result.changes[0]
        assert change.exact
        assert change.relative is None

    def test_a_count_change_is_reported_even_when_it_is_proportionally_tiny(self) -> None:
        result = compare(
            {**BRACKET, "face_count": 100_000}, {**BRACKET, "face_count": 100_001}
        )

        assert result

    def test_a_solid_that_split_in_two_is_reported(self) -> None:
        # The change most likely to be missed by a volume comparison: a boolean
        # that separated the part leaves the volume alone.
        result = compare(BRACKET, {**BRACKET, "solid_count": 2})

        assert result
        assert result.changes[0].name == "solid_count"


class TestVectors:
    def test_a_moved_bounding_box_is_one_change_not_three(self) -> None:
        result = compare(
            BRACKET, {**BRACKET, "bounding_box_max_mm": [130.0, 80.0, 10.0]}
        )

        assert len(result.changes) == 1
        assert result.changes[0].name == "bounding_box_max_mm"

    def test_a_vector_within_tolerance_on_every_axis_is_no_change(self) -> None:
        nudged = [v * (1 + RELATIVE_TOLERANCE / 10) if v else v for v in [120.0, 80.0, 10.0]]

        assert not compare(BRACKET, {**BRACKET, "bounding_box_max_mm": nudged})

    def test_vectors_of_different_length_are_not_paired_off(self) -> None:
        """Pairing them would compare a 3-vector's x against a 2-vector's x and
        silently drop the rest."""
        result = compare(BRACKET, {**BRACKET, "centre_of_mass_mm": [60.0, 40.0]})

        assert "centre_of_mass_mm" in result.unmeasured
        assert not result.complete


class TestIncompleteComparisons:
    def test_a_quantity_measured_on_one_side_only_is_unmeasured_not_changed(self) -> None:
        """The recovery differs: a change is reviewed, a missing measurement
        means one version was never fully inspected."""
        without = {key: value for key, value in BRACKET.items() if key != "volume_mm3"}

        result = compare(BRACKET, without)

        assert "volume_mm3" in result.unmeasured
        assert not any(change.name == "volume_mm3" for change in result.changes)

    def test_an_incomplete_comparison_must_not_read_as_clean(self) -> None:
        """The same rule Decision 3 applies to an unmeasured assertion: nobody
        checked is never fine."""
        without = {key: value for key, value in BRACKET.items() if key != "volume_mm3"}

        result = compare(BRACKET, without)

        assert not result.complete
        assert "incomplete" in result.summary()
        assert "not evidence that nothing else moved" in result.summary()

    def test_a_quantity_absent_from_both_sides_is_neither(self) -> None:
        # Two versions that were both inspected by a reader that does not
        # measure mass are comparable; the comparison simply does not cover mass.
        slim = {"volume_mm3": 100.0, "face_count": 6}

        result = compare(slim, slim)

        assert result.complete
        assert not result


class TestTheDict:
    def test_it_carries_the_tolerance_it_was_judged_against(self) -> None:
        # A reviewer reading "no change" has to be able to ask "within what".
        payload = compare(BRACKET, dict(BRACKET)).to_dict()

        assert payload["tolerance"] == RELATIVE_TOLERANCE
        assert payload["changed"] is False
        assert payload["complete"] is True

    def test_a_change_carries_both_sides_and_its_unit(self) -> None:
        payload = compare(BRACKET, moved("volume_mm3", 1.5)).to_dict()

        change = payload["changes"][0]
        assert change["before"] == 96_000.0
        assert change["after"] == 144_000.0
        assert change["unit"] == "mm³"
