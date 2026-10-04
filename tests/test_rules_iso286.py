"""ISO 286-1:2010 read back against its own worked examples -- master plan 13.2.

**Why the standard is its own oracle here.** A transcribed table can only be wrong in one
way -- a cell that says something the page does not -- and the usual defences do not reach
it: a type checker sees valid floats, a reviewer's eye slides over the four hundredth
number, and a wrong deviation produces a part that is machinable, measurable and quietly
out of tolerance. What *does* reach it is that ISO 286-1 prints nine fully worked
transformations, in 4.3.2.4, 4.3.2.5, 4.3.3 and Annex B, and each one exercises a
different corner: the two directions of Figures 8 and 9, the |delta| rule on a hole that
straddles the nominal size, the |delta| rule on an interference fit, a class read from
ISO 286-2 instead, and three complete fits with their clearances. Reproducing all nine
from Tables 1 to 5 alone is a much stronger claim than any spot check.

The second defence is `TestTheShaftTableIsTheHoleTableNegated`: Tables 4 and 5 are
transcribed *independently* here, from the printed pages, and compared against the hole
tables the module actually ships. Two transcriptions that agree are evidence; one is a
hope.
"""

from __future__ import annotations

import pytest

from app.rules.fits import Feature, FitKind
from app.rules.iso286 import (
    Iso286Error,
    delta_um,
    deviations_mm,
    fit,
    limit_deviations_um,
    limits_mm,
    parse_class,
    standard_tolerance_um,
    zone,
)


class TestTheStandardsOwnWorkedExamples:
    """The nine transformations ISO 286-1 prints in full, reproduced from Tables 1 to 5.

    Each one names the clause it comes from, so a failure sends the reader to a page
    rather than to a guess.
    """

    def test_90_F7_is_plus_36_to_plus_71(self) -> None:
        """4.3.2.4 EXAMPLE 1: IT7 = 35, EI = +36 from Table 2, ES = EI + IT."""
        assert limit_deviations_um(90, "F7") == pytest.approx((36.0, 71.0))

    def test_90_f7_is_minus_36_to_minus_71(self) -> None:
        """4.3.2.4 EXAMPLE 2: the shaft is the mirror, es = -36 and ei = es - IT."""
        assert limit_deviations_um(90, "f7") == pytest.approx((-71.0, -36.0))

    def test_28_P9_is_minus_22_to_minus_74(self) -> None:
        """4.3.2.4 EXAMPLE 3. P9 is above IT7, so no |delta| is added -- which is the whole
        point of the example sitting *before* 4.3.2.5 rather than after it."""
        assert limit_deviations_um(28, "P9") == pytest.approx((-74.0, -22.0))

    def test_20_K7_straddles_the_nominal_size(self) -> None:
        """4.3.2.5 EXAMPLE 1: ES = -2 + |delta| = -2 + 8 = +6, EI = ES - 21 = -15.

        Without the |delta| rule this reads -2/-23 -- a clearance zone instead of a
        transition one, and nothing about it looks wrong.
        """
        assert limit_deviations_um(20, "K7") == pytest.approx((-15.0, 6.0))

    def test_40_U6_is_an_interference_class(self) -> None:
        """4.3.2.5 EXAMPLE 2: ES = -60 + 5 = -55, EI = -55 - 16 = -71."""
        assert limit_deviations_um(40, "U6") == pytest.approx((-71.0, -55.0))

    def test_60_M6_matches_what_iso_286_2_tabulates(self) -> None:
        """4.3.3's example is read from ISO 286-2's Table 9, which this repository does
        not have. Part 1's tables reproduce it, which is the evidence that Part 2 is a
        convenience here and not a missing dependency."""
        assert limit_deviations_um(60, "M6") == pytest.approx((-24.0, -5.0))

    def test_36_H8_over_f7_is_a_clearance_fit(self) -> None:
        """Annex B EXAMPLE 1: 0,025 mm minimum and 0,089 mm maximum clearance."""
        made = fit(36, "H8/f7")

        assert made.kind is FitKind.CLEARANCE
        assert made.smallest_clearance_mm == pytest.approx(0.025)
        assert made.largest_clearance_mm == pytest.approx(0.089)

    def test_36_H7_over_n6_is_a_transition_fit(self) -> None:
        """Annex B EXAMPLE 2: it can go either way -- 0,008 clearance or 0,033 interference."""
        made = fit(36, "H7/n6")

        assert made.kind is FitKind.TRANSITION
        assert made.largest_clearance_mm == pytest.approx(0.008)
        assert made.smallest_clearance_mm == pytest.approx(-0.033)

    def test_36_H7_over_s6_is_an_interference_fit(self) -> None:
        """Annex B EXAMPLE 3: 0,018 minimum and 0,059 maximum interference."""
        made = fit(36, "H7/s6")

        assert made.kind is FitKind.INTERFERENCE
        assert made.largest_clearance_mm == pytest.approx(-0.018)
        assert made.smallest_clearance_mm == pytest.approx(-0.059)

    def test_the_annex_b_spans_come_out_of_the_same_three_fits(self) -> None:
        """B.3, which is arithmetic on the three results above and so checks them again
        from a direction the individual assertions do not."""
        clearance = fit(36, "H8/f7")
        transition = fit(36, "H7/n6")
        interference = fit(36, "H7/s6")

        spans = (
            clearance.largest_clearance_mm - clearance.smallest_clearance_mm,
            transition.largest_clearance_mm - transition.smallest_clearance_mm,
            interference.largest_clearance_mm - interference.smallest_clearance_mm,
        )
        assert spans == pytest.approx((0.064, 0.041, 0.041))


class TestTableOne:
    """Standard tolerance values, including the two corners that are easy to lose."""

    @pytest.mark.parametrize(
        ("nominal_mm", "grade", "expected_um"),
        [
            (1.0, "01", 0.3), (1.0, "18", 1400.0),
            (90.0, "7", 35.0), (28.0, "9", 52.0), (20.0, "7", 21.0),
            (40.0, "6", 16.0), (60.0, "6", 19.0), (36.0, "8", 39.0),
            (500.0, "1", 8.0), (3150.0, "18", 33000.0), (3150.0, "1", 26.0),
        ],
    )
    def test_the_grade_is_read_off_the_right_row(
        self, nominal_mm: float, grade: str, expected_um: float
    ) -> None:
        assert standard_tolerance_um(nominal_mm, grade) == pytest.approx(expected_um)

    def test_a_band_is_closed_above_and_open_below(self) -> None:
        """30 mm is in the 18-to-30 row, not the 30-to-50 one. Off by one here moves
        every deviation on the boundary sizes, which are the common ones."""
        assert standard_tolerance_um(30.0, "7") == pytest.approx(21.0)
        assert standard_tolerance_um(30.0001, "7") == pytest.approx(25.0)

    def test_the_two_finest_grades_stop_at_500_mm(self) -> None:
        assert standard_tolerance_um(500.0, "01") == pytest.approx(4.0)
        with pytest.raises(Iso286Error, match="up to and including 500"):
            standard_tolerance_um(501.0, "01")

    def test_above_3150_mm_there_is_no_table(self) -> None:
        with pytest.raises(Iso286Error, match="is above the 3150 mm where"):
            standard_tolerance_um(3151.0, "7")


class TestTheDeltaRule:
    """4.3.2.5 -- the correction whose absence is invisible in the answer."""

    @pytest.mark.parametrize(
        ("nominal_mm", "grade", "expected"),
        [(20.0, "7", 8.0), (40.0, "6", 5.0), (60.0, "6", 6.0), (1.0, "7", 0.0)],
    )
    def test_the_delta_values_come_off_table_3(
        self, nominal_mm: float, grade: str, expected: float
    ) -> None:
        assert delta_um(nominal_mm, grade) == pytest.approx(expected)

    def test_it_applies_to_K_M_and_N_up_to_IT8_and_not_above(self) -> None:
        """K8 takes the |delta|, K9 does not -- and Table 2's "above IT8" column for K is
        zero, so the two answers are not a near miss of each other."""
        assert limit_deviations_um(20, "K8")[1] == pytest.approx(-2.0 + 12.0)
        assert limit_deviations_um(20, "K9")[1] == pytest.approx(0.0)

    def test_it_applies_to_P_and_beyond_only_up_to_IT7(self) -> None:
        """P7 takes it, P8 does not. 4.3.2.4's own EXAMPLE 3 is the P9 half of this."""
        assert limit_deviations_um(28, "P7")[1] == pytest.approx(-22.0 + 8.0)
        assert limit_deviations_um(28, "P8")[1] == pytest.approx(-22.0)

    def test_without_it_a_transition_class_reads_as_a_clearance_one(self) -> None:
        """The consequence, stated as a claim rather than as a number: 20 K7 contains the
        nominal size, and the uncorrected -2/-23 does not."""
        lower, upper = limit_deviations_um(20, "K7")

        assert lower < 0.0 < upper, "K7 is a transition class and must straddle nominal"


class TestTheShaftTableIsTheHoleTableNegated:
    """Tables 4 and 5, transcribed here a second time, against the hole tables shipped.

    4.3.2.3 makes a shaft's fundamental deviation the negation of the hole's of the same
    letter, and the module relies on it rather than carrying a fourth table. That reliance
    is only safe if it was *checked* against the printed shaft pages, which is what these
    values are: read off Tables 4 and 5 independently of Tables 2 and 3.
    """

    @pytest.mark.parametrize(
        ("nominal_mm", "shaft_class", "es_um"),
        [
            # Table 4, band 0 to 3 -- every letter it carries.
            (2.0, "a7", -270.0), (2.0, "b7", -140.0), (2.0, "c7", -60.0),
            (2.0, "cd7", -34.0), (2.0, "d7", -20.0), (2.0, "e7", -14.0),
            (2.0, "ef7", -10.0), (2.0, "f7", -6.0), (2.0, "fg7", -4.0),
            (2.0, "g7", -2.0), (2.0, "h7", 0.0),
            # Band 10 to 18, where cd, ef and fg still have columns.
            (12.0, "a7", -290.0), (12.0, "cd7", -70.0), (12.0, "d7", -50.0),
            (12.0, "e7", -32.0), (12.0, "ef7", -23.0), (12.0, "f7", -16.0),
            (12.0, "fg7", -10.0), (12.0, "g7", -6.0),
            # 18 to 30, the band where Table 4 prints -25 for ef and Table 2 prints +28
            # for EF. Pinned here as well as in TestTheThreeCellsThatAreNotArithmetic so
            # the decision is caught from the shaft side too, which is where the misprint
            # actually sits.
            (24.0, "ef7", -28.0), (24.0, "e7", -40.0), (24.0, "f7", -20.0),
            (24.0, "cd7", -85.0), (24.0, "fg7", -12.0),
            # Band 30 to 50, the last with cd, ef and fg.
            (35.0, "a7", -310.0), (45.0, "a7", -320.0), (35.0, "cd7", -100.0),
            (35.0, "d7", -80.0), (35.0, "e7", -50.0), (35.0, "ef7", -35.0),
            (35.0, "f7", -25.0), (35.0, "fg7", -15.0), (35.0, "g7", -9.0),
            # Larger bands, where only d to h survive.
            (90.0, "a7", -380.0), (90.0, "b7", -220.0), (90.0, "c7", -170.0),
            (90.0, "d7", -120.0), (90.0, "e7", -72.0), (90.0, "f7", -36.0),
            (90.0, "g7", -12.0),
            (420.0, "a7", -1500.0), (480.0, "a7", -1650.0), (420.0, "d7", -230.0),
            (420.0, "e7", -135.0), (420.0, "f7", -68.0), (420.0, "g7", -20.0),
            (3000.0, "d7", -520.0), (3000.0, "e7", -290.0), (3000.0, "f7", -145.0),
            (3000.0, "g7", -38.0), (3000.0, "h7", 0.0),
        ],
    )
    def test_the_upper_deviation_of_a_to_h_is_table_4s_own_value(
        self, nominal_mm: float, shaft_class: str, es_um: float
    ) -> None:
        assert limit_deviations_um(nominal_mm, shaft_class)[1] == pytest.approx(es_um)

    @pytest.mark.parametrize(
        ("nominal_mm", "shaft_class", "ei_um"),
        [
            # Table 5, band 0 to 3.
            (2.0, "m6", 2.0), (2.0, "n6", 4.0), (2.0, "p6", 6.0), (2.0, "r6", 10.0),
            (2.0, "s6", 14.0), (2.0, "u6", 18.0), (2.0, "x6", 20.0), (2.0, "z6", 26.0),
            (2.0, "za6", 32.0), (2.0, "zb6", 40.0), (2.0, "zc6", 60.0),
            # Band 30 to 40, where every letter including t, v and y has a column.
            (35.0, "m6", 9.0), (35.0, "n6", 17.0), (35.0, "p6", 26.0), (35.0, "r6", 34.0),
            (35.0, "s6", 43.0), (35.0, "t6", 48.0), (35.0, "u6", 60.0), (35.0, "v6", 68.0),
            (35.0, "x6", 80.0), (35.0, "y6", 94.0), (35.0, "z6", 112.0),
            (35.0, "za6", 148.0), (35.0, "zb6", 200.0), (35.0, "zc6", 274.0),
            # Band 120 to 140, on the finer subdivision.
            (130.0, "p6", 43.0), (130.0, "r6", 63.0), (130.0, "s6", 92.0),
            (130.0, "t6", 122.0), (130.0, "u6", 170.0), (130.0, "v6", 202.0),
            (130.0, "x6", 248.0), (130.0, "y6", 300.0), (130.0, "z6", 365.0),
            (130.0, "za6", 470.0), (130.0, "zb6", 620.0), (130.0, "zc6", 800.0),
            # The last band, where only m to u survive.
            (3000.0, "m6", 76.0), (3000.0, "n6", 135.0), (3000.0, "p6", 240.0),
            (3000.0, "r6", 580.0), (3000.0, "s6", 1400.0), (3000.0, "t6", 2100.0),
            (3000.0, "u6", 3200.0),
        ],
    )
    def test_the_lower_deviation_of_m_to_zc_is_table_5s_own_value(
        self, nominal_mm: float, shaft_class: str, ei_um: float
    ) -> None:
        assert limit_deviations_um(nominal_mm, shaft_class)[0] == pytest.approx(ei_um)

    @pytest.mark.parametrize(
        ("nominal_mm", "shaft_class", "ei_um"),
        [
            (2.0, "j5", -2.0), (2.0, "j7", -4.0), (2.0, "j8", -6.0),
            (60.0, "j5", -7.0), (60.0, "j6", -7.0), (60.0, "j7", -12.0),
            (90.0, "j5", -9.0), (90.0, "j6", -9.0), (90.0, "j7", -15.0),
            (420.0, "j6", -20.0), (420.0, "j7", -32.0),
            (4.0, "k5", 1.0), (4.0, "k7", 1.0), (60.0, "k6", 2.0), (90.0, "k6", 3.0),
            (420.0, "k6", 5.0), (380.0, "k6", 4.0),
        ],
    )
    def test_j_and_k_are_transcribed_rather_than_negated(
        self, nominal_mm: float, shaft_class: str, ei_um: float
    ) -> None:
        """These two group their grades differently from their upper-case twins, so a
        negation would be wrong for them however right it is for everything else."""
        assert limit_deviations_um(nominal_mm, shaft_class)[0] == pytest.approx(ei_um)

    def test_shaft_k_is_zero_at_IT3_and_above_IT7(self) -> None:
        """Table 5's second k column. k6 is +2 at 40 mm and k3 and k8 are both 0."""
        assert limit_deviations_um(40, "k6")[0] == pytest.approx(2.0)
        assert limit_deviations_um(40, "k3")[0] == pytest.approx(0.0)
        assert limit_deviations_um(40, "k8")[0] == pytest.approx(0.0)


class TestTheThreeCellsThatAreNotArithmetic:
    """Each of these is stated by the standard as an exception, and each is invisible."""

    def test_the_ef_misprint_is_resolved_towards_the_geometric_mean(self) -> None:
        """Table 2 gives EF at 18 to 30 mm as +28 and Table 4 gives ef as -25. ISO builds
        the intermediate deviations as the geometric mean of their neighbours, and
        sqrt(e*f) = sqrt(40*20) = 28,28 -- so 28 is the value and -25 is the misprint. This
        test exists so the decision is visible rather than buried in one cell."""
        e = -limit_deviations_um(24, "e7")[1]
        f = -limit_deviations_um(24, "f7")[1]

        assert (e, f) == pytest.approx((40.0, 20.0))
        assert -limit_deviations_um(24, "ef7")[1] == pytest.approx(28.0)
        assert round((e * f) ** 0.5) == 28

    def test_N_above_IT8_is_zero_except_in_the_first_band(self) -> None:
        """N9 is 0/-30 at 4 mm and -4/-29 at 2 mm, which ISO 286-2 tabulates both ways."""
        assert limit_deviations_um(4, "N9") == pytest.approx((-30.0, 0.0))
        assert limit_deviations_um(2, "N9") == pytest.approx((-29.0, -4.0))

    def test_M6_between_250_and_315_mm_is_minus_9(self) -> None:
        """Footnote b to Table 2: -9, "instead of -11 according to the calculation"."""
        assert limit_deviations_um(300, "M6")[1] == pytest.approx(-9.0)
        assert limit_deviations_um(300, "M7")[1] == pytest.approx(-20.0 + 20.0)


class TestItRefusesRatherThanInterpolating:
    """An empty cell is not a small number, and a prohibition is not an empty cell."""

    @pytest.mark.parametrize(
        ("nominal_mm", "tolerance_class"),
        [(20.0, "T7"), (12.0, "V7"), (16.0, "Y7"), (60.0, "CD7"), (60.0, "EF7"),
         (60.0, "FG7"), (600.0, "A7"), (600.0, "J7"), (600.0, "ZC7"), (20.0, "t7"),
         (12.0, "v6"), (16.0, "y6"), (600.0, "cd7")],
    )
    def test_a_letter_with_no_column_in_that_band_is_refused(
        self, nominal_mm: float, tolerance_class: str
    ) -> None:
        with pytest.raises(Iso286Error, match="is not in ISO 286-1"):
            limit_deviations_um(nominal_mm, tolerance_class)

    def test_A_and_B_are_refused_at_or_below_1_mm_because_the_footnote_forbids_them(
        self,
    ) -> None:
        """The table *has* a value at 1 mm. Footnote a is what makes using it wrong, so
        the refusal quotes the footnote rather than claiming the cell is empty."""
        with pytest.raises(Iso286Error, match="shall not be used at or below 1 mm"):
            limit_deviations_um(1.0, "A11")
        with pytest.raises(Iso286Error, match="shall not be used at or below 1 mm"):
            limit_deviations_um(0.8, "b11")
        assert limit_deviations_um(1.5, "A11")[0] == pytest.approx(270.0)

    def test_N_above_IT8_is_refused_at_or_below_1_mm(self) -> None:
        with pytest.raises(Iso286Error, match="N above\nIT8|N above IT8"):
            limit_deviations_um(0.9, "N9")

    @pytest.mark.parametrize(
        "bad", ["", "7", "H", "Hh7", "H19", "I7", "l7", "O7", "q7", "W7", "H7/g6"]
    )
    def test_something_that_is_not_a_tolerance_class_is_said_to_be_one(
        self, bad: str
    ) -> None:
        with pytest.raises(Iso286Error):
            parse_class(bad)

    def test_a_size_that_is_no_feature_is_refused(self) -> None:
        with pytest.raises(Iso286Error, match="no feature of size"):
            limit_deviations_um(0.0, "H7")
        with pytest.raises(Iso286Error, match="no feature of size"):
            limit_deviations_um(-10.0, "H7")

    def test_a_fit_needs_both_members(self) -> None:
        with pytest.raises(Iso286Error, match="names both members"):
            fit(36, "H7")


class TestItJoinsUpWithTheFitArithmetic:
    """The point of the module: `fits.py` no longer needs the caller to supply a zone."""

    def test_a_zone_carries_the_standard_as_its_source(self) -> None:
        made = zone(36, "H8")

        assert made.feature is Feature.HOLE
        assert made.nominal_mm == pytest.approx(36.0)
        assert made.lower_deviation_mm == pytest.approx(0.0)
        assert made.upper_deviation_mm == pytest.approx(0.039)
        assert "ISO 286-1:2010" in made.source
        assert made.designation == "36 H8"

    def test_case_is_the_whole_of_the_hole_and_shaft_distinction(self) -> None:
        """H7 and h7 are different classes on the same nominal size, and mistaking one for
        the other reverses the fit."""
        assert zone(36, "H7").feature is Feature.HOLE
        assert zone(36, "h7").feature is Feature.SHAFT
        assert limit_deviations_um(36, "H7") == pytest.approx((0.0, 25.0))
        assert limit_deviations_um(36, "h7") == pytest.approx((-25.0, 0.0))

    def test_js_and_JS_straddle_the_nominal_size_symmetrically(self) -> None:
        """NOTE 3 to 4.1: neither has a fundamental deviation at all."""
        assert limit_deviations_um(80, "js15") == pytest.approx((-600.0, 600.0))
        assert limit_deviations_um(80, "JS15") == pytest.approx((-600.0, 600.0))

    def test_the_example_of_4_2_2_reads_back_in_millimetres(self) -> None:
        """4.2.2 EXAMPLE 1 gives all three in the + and - form: 32 H7, 80 js15, 100 g6."""
        assert deviations_mm(32, "H7") == pytest.approx((0.0, 0.025))
        assert deviations_mm(80, "js15") == pytest.approx((-0.6, 0.6))
        assert deviations_mm(100, "g6") == pytest.approx((-0.034, -0.012))

    def test_the_limits_are_the_sizes_a_part_may_actually_be(self) -> None:
        assert limits_mm(36, "H8") == pytest.approx((36.0, 36.039))
        assert limits_mm(36, "f7") == pytest.approx((35.95, 35.975))

    def test_a_fit_designation_may_carry_its_nominal_size(self) -> None:
        """5.2.1 writes a fit as 36 H8/f7; the size is already an argument, so the two
        spellings must agree rather than one of them being refused."""
        assert fit(36, "36 H8/f7").name == fit(36, "H8/f7").name

    def test_every_grade_of_a_selected_class_widens_monotonically(self) -> None:
        """Not from the standard -- a property that must hold of any correct reading, and
        one that a single mistyped IT cell breaks."""
        previous = 0.0
        for grade in [str(n) for n in range(1, 19)]:
            width = standard_tolerance_um(50, grade)
            assert width > previous, f"IT{grade} is not wider than IT{int(grade) - 1}"
            previous = width
