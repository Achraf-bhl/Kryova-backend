"""EN 1993-1-9 read into code: the weld catalogue, the size factor, γMf, §7.2.1 and §8.

Every number asserted as a literal here was read off a page of the standard, and the test says
which. `docs/eurocode3-fatigue-reading.md` holds the page map.
"""

from __future__ import annotations

import math
from dataclasses import fields

import pytest

from app.design.assertions import Outcome
from app.fatigue.eurocode3 import (
    DIRECT_CATEGORIES_MPA,
    SHEAR_CATEGORIES_MPA,
    AssessmentMethod,
    Consequence,
    EquivalentRange,
    PartialFactor,
    StressKind,
    WeldState,
    check_range_limit,
    eccentric_step_size_factor,
    effective_collective,
    effective_range_mpa,
    range_limit_mpa,
    recommended_gamma_mf,
    require_category,
    thickness_size_factor,
    verify,
)
from app.fatigue.history import Collective, SignConvention, StressBasis
from app.fatigue.material import EC3_FAILURE_PROBABILITY, ShearDetail, WeldDetail
from app.fatigue.weld_catalogue import (
    FACTS,
    PAGES,
    REFERRED_ELSEWHERE,
    ROWS,
    Band,
    Classification,
    ClassificationError,
    Fact,
    Joint,
    Row,
    Table,
    classify,
)

NOMINAL = StressBasis.NOMINAL


def _one(joint: Joint, facts: dict[str, bool | float], stress: StressKind = StressKind.DIRECT) -> float:
    categories = classify(joint, facts, basis=NOMINAL).categories(stress)
    assert len(categories) == 1, categories
    return categories[0]


def _row(table: Table, detail: int, category: float) -> Row:
    return next(r for r in ROWS if r.table is table and detail in r.details and r.category_mpa == category)


class TestTheCategoriesAreTheFiguresOwn:
    def test_figure_7_1_and_7_2_draw_these_categories(self) -> None:
        # Read off the curve labels of Figure 7.1 (p. 15) at 400 dpi, and Figure 7.2 (p. 16).
        assert DIRECT_CATEGORIES_MPA == (160, 140, 125, 112, 100, 90, 80, 71, 63, 56, 50, 45, 40, 36)
        assert SHEAR_CATEGORIES_MPA == (100, 80)

    @pytest.mark.parametrize(
        ("category", "stress"), [(75.0, StressKind.DIRECT), (180.0, StressKind.DIRECT), (90.0, StressKind.SHEAR)]
    )
    def test_a_category_between_two_curves_is_refused(self, category: float, stress: StressKind) -> None:
        with pytest.raises(ValueError, match="not a .* stress detail category"):
            require_category(category, stress)

    def test_every_row_sits_on_a_drawn_curve_on_a_page_of_its_table(self) -> None:
        for row in ROWS:
            assert require_category(row.category_mpa, row.stress) == row.category_mpa
            assert row.page in PAGES[row.table]

    def test_a_row_with_an_undrawn_category_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="not a direct stress detail category"):
            Row(Table.T8_3, (1,), 75.0, (Joint.PLATE_SPLICE,), 22, "a splice")

    def test_a_row_on_a_page_its_table_is_not_on_is_refused(self) -> None:
        with pytest.raises(ValueError, match="page"):
            Row(Table.T8_4, (1,), 80.0, (Joint.LONGITUDINAL_ATTACHMENT,), 25, "an attachment")

    def test_a_row_conditioned_on_a_fact_the_catalogue_does_not_read_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a fact"):
            Row(Table.T8_3, (1,), 112.0, (Joint.PLATE_SPLICE,), 22, "a splice", conditions=(Fact("polished", True),))

    def test_a_number_fact_cannot_be_a_yes_no_condition(self) -> None:
        with pytest.raises(ValueError, match="number"):
            Row(Table.T8_3, (1,), 112.0, (Joint.PLATE_SPLICE,), 22, "a splice", conditions=(Fact("t_mm", True),))


class TestTheCatalogueHoldsTheTablesRead:
    def test_every_detail_on_the_pages_read_is_in_the_catalogue(self) -> None:
        details = {table: {d for r in ROWS if r.table is table for d in r.details} for table in Table}
        # Table 8.3 detail 19 is "According to Table 8.4, detail 4" (p. 23); Table 8.5 detail 10
        # refers to EN 1994-2 (p. 25).
        assert details[Table.T8_3] == set(range(1, 19))
        assert details[Table.T8_4] == set(range(1, 10))
        assert details[Table.T8_5] == {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12}
        assert details[Table.B_1] == set(range(1, 8))

    def test_every_joint_has_a_row_or_a_referral(self) -> None:
        for joint in Joint:
            assert any(joint in r.joints for r in ROWS) or joint in REFERRED_ELSEWHERE, joint

    def test_a_joint_the_table_sends_elsewhere_has_no_candidate_and_names_where(self) -> None:
        result = classify(Joint.STUD_SHEAR_CONNECTOR, basis=NOMINAL)
        assert result.candidates == ()
        assert result.uncovered is not None and "EN 1994-2" in result.uncovered

    def test_detail_19_is_classified_as_table_8_4_detail_4_and_owes_a_second_check(self) -> None:
        result = classify(Joint.INTERSECTING_FLANGES_BUTT, basis=NOMINAL)
        labels = {(c.row.table, c.row.details) for c in result.candidates}
        assert (Table.T8_3, (18,)) in labels and (Table.T8_4, (4,)) in labels
        assert any("Table 8.4, detail 4 or detail 5" in text for text in result.also)

    def test_table_b_1_is_hot_spot_and_the_others_nominal(self) -> None:
        for row in ROWS:
            assert row.basis is (StressBasis.HOT_SPOT if row.table is Table.B_1 else StressBasis.NOMINAL)

    def test_the_shear_rows_are_table_8_5_on_slope_five(self) -> None:
        shear = [r for r in ROWS if r.stress is StressKind.SHEAR]
        assert {(r.table, r.details) for r in shear} == {(Table.T8_5, (3,)), (Table.T8_5, (8,)), (Table.T8_5, (9,))}
        assert all(r.slope == 5.0 and r.category_mpa == 80.0 for r in shear)

    def test_the_asterisks_are_where_the_page_puts_them(self) -> None:
        starred = {(r.table, r.details, r.category_mpa) for r in ROWS if r.asterisk}
        assert starred == {(Table.T8_5, (3,), 36.0), (Table.T8_5, (5,), 45.0), (Table.T8_5, (6,), 56.0)}


class TestAClassificationNeverPicks:
    def test_a_classification_has_nothing_that_chooses(self) -> None:
        names = {f.name for f in fields(Classification)} | set(dir(Classification))
        assert not names & {"best", "pick", "picked", "chosen", "selected", "category"}

    def test_with_nothing_known_every_row_of_the_joint_is_a_candidate(self) -> None:
        for joint in Joint:
            if joint in REFERRED_ELSEWHERE:
                continue
            rows = [r for r in ROWS if joint in r.joints]
            basis = rows[0].basis
            result = classify(joint, basis=basis)
            assert len(result.candidates) == len(rows), joint
            assert result.excluded == ()

    def test_a_plate_splice_with_nothing_known_is_anything_from_112_to_36(self) -> None:
        result = classify(Joint.PLATE_SPLICE, basis=NOMINAL)
        assert result.categories() == (112, 90, 80, 71, 50, 36)
        conservative = result.conservative()
        assert conservative is not None and conservative.row == _row(Table.T8_3, 13, 36.0)
        assert {"welded_both_sides", "checked_by_ndt", "ground_flush", "backing_strip"} <= set(result.undecided)

    def test_a_fact_excludes_only_the_rows_that_state_it(self) -> None:
        result = classify(Joint.PLATE_SPLICE, {"welded_both_sides": True}, basis=NOMINAL)
        excluded = {(row.details, row.category_mpa): why for row, why in result.excluded}
        assert excluded == {((13,), 36.0): "welded_both_sides = no", ((13,), 71.0): "welded_both_sides = no"}
        assert result.categories() == (112, 90, 80, 71, 50)

    def test_a_ground_flush_two_sided_splice_without_backing_is_112_or_90(self) -> None:
        # The 90 row (details 5–7, p. 22) states a convexity limit, not that the weld is unground,
        # so read literally it is not excluded by a flush weld. The engineer chooses between them.
        facts = {"welded_both_sides": True, "backing_strip": False, "ground_flush": True}
        result = classify(Joint.PLATE_SPLICE, facts, basis=NOMINAL)
        assert result.categories() == (112, 90)
        conservative = result.conservative()
        assert conservative is not None and conservative.row.category_mpa == 90

    def test_a_plate_splice_without_ndt_is_outside_table_8_3(self) -> None:
        facts = {"welded_both_sides": True, "backing_strip": False, "checked_by_ndt": False}
        result = classify(Joint.PLATE_SPLICE, facts, basis=NOMINAL)
        assert result.candidates == () and result.conservative() is None
        assert result.uncovered is not None and "checked_by_ndt = yes" in result.uncovered

    def test_a_one_sided_weld_checked_by_ndt_keeps_its_36_row(self) -> None:
        # Detail 13's 36 row (p. 23) names no inspection, so NDT does not exclude it.
        facts = {"welded_both_sides": False, "checked_by_ndt": True, "backing_strip": False}
        assert classify(Joint.PLATE_SPLICE, facts, basis=NOMINAL).categories() == (71, 36)

    def test_a_candidate_is_supported_only_when_every_condition_was_confirmed(self) -> None:
        facts = {"welded_both_sides": False, "checked_by_ndt": True, "backing_strip": False}
        result = classify(Joint.PLATE_SPLICE, facts, basis=NOMINAL)
        assert all(c.supported for c in result.candidates)
        partial = classify(Joint.PLATE_SPLICE, {"welded_both_sides": False}, basis=NOMINAL)
        by_category = {c.row.category_mpa: c for c in partial.candidates}
        assert not by_category[36.0].supported and by_category[36.0].open == ("backing_strip = no",)

    def test_detail_16_is_a_candidate_while_either_of_its_alternatives_holds(self) -> None:
        base = {"welded_both_sides": True, "backing_strip": True, "checked_by_ndt": False}
        fit_unknown = {**base, "backing_strip_fillets_end_10mm_or_more_from_edges": True}
        assert 50.0 in classify(Joint.PLATE_SPLICE, fit_unknown, basis=NOMINAL).categories()
        both_good = {**fit_unknown, "backing_strip_good_fit_guaranteed": True}
        assert classify(Joint.PLATE_SPLICE, both_good, basis=NOMINAL).categories() == (71,)

    def test_direct_and_shear_are_two_assessments_not_two_alternatives(self) -> None:
        result = classify(Joint.CRUCIFORM_OR_TEE_ROOT, basis=NOMINAL)
        direct = result.conservative(StressKind.DIRECT)
        shear = result.conservative(StressKind.SHEAR)
        assert direct is not None and direct.row.category_mpa == 36 and direct.row.asterisk
        assert shear is not None and shear.row.category_mpa == 80


class TestTheNumbersChooseTheRow:
    @pytest.mark.parametrize(
        ("length", "category"), [(40.0, 80.0), (50.0, 80.0), (50.5, 71.0), (80.0, 71.0), (100.0, 63.0), (100.5, 56.0)]
    )
    def test_a_longitudinal_attachment_is_classed_by_its_length(self, length: float, category: float) -> None:
        # Table 8.4 detail 1 (p. 24): L≤50 80, 50<L≤80 71, 80<L≤100 63, L>100 56.
        facts = {"L_mm": length, "alpha_deg": 60.0, "attachment_thinner_than_its_height": True}
        assert _one(Joint.LONGITUDINAL_ATTACHMENT, facts) == category

    def test_a_long_attachment_with_an_unknown_end_angle_may_also_be_detail_2(self) -> None:
        facts = {"L_mm": 150.0, "attachment_thinner_than_its_height": True}
        assert classify(Joint.LONGITUDINAL_ATTACHMENT, facts, basis=NOMINAL).categories() == (71, 56)

    @pytest.mark.parametrize(
        ("ell", "t", "category"),
        [
            (40.0, 10.0, 80.0), (60.0, 10.0, 71.0), (90.0, 10.0, 63.0), (110.0, 10.0, 56.0),
            (150.0, 15.0, 56.0), (150.0, 25.0, 50.0), (250.0, 25.0, 50.0), (250.0, 40.0, 45.0),
            (400.0, 40.0, 45.0), (400.0, 60.0, 40.0),
        ],
    )
    def test_a_cruciform_toe_is_classed_by_l_and_t(self, ell: float, t: float, category: float) -> None:
        # Table 8.5 detail 1 (p. 25, read at 300 dpi).
        facts = {"l_mm": ell, "t_mm": t, "inspected_free_of_discontinuities": True, "misalignment_ratio": 0.1}
        assert _one(Joint.CRUCIFORM_OR_TEE_TOE, facts) == category

    def test_l_of_50_mm_is_covered_by_no_row_and_both_neighbours_come_back_flagged(self) -> None:
        # "ℓ<50 mm" and "50<ℓ≤80": the page says nothing of ℓ = 50.
        result = classify(Joint.CRUCIFORM_OR_TEE_TOE, {"l_mm": 50.0}, basis=NOMINAL)
        assert result.categories() == (80, 71)
        assert all(c.on_boundary and not c.supported for c in result.candidates)
        conservative = result.conservative()
        assert conservative is not None and conservative.row.category_mpa == 71

    def test_an_inclusive_bound_is_not_a_gap(self) -> None:
        facts = {"L_mm": 50.0, "alpha_deg": 60.0, "attachment_thinner_than_its_height": True}
        result = classify(Joint.LONGITUDINAL_ATTACHMENT, facts, basis=NOMINAL)
        assert [c.row.category_mpa for c in result.candidates] == [80.0]
        assert result.candidates[0].on_boundary == ()

    @pytest.mark.parametrize(
        ("t_c", "t", "category"),
        [
            (10.0, 15.0, 56.0), (10.0, 25.0, 50.0), (10.0, 40.0, 45.0), (10.0, 60.0, 40.0),
            (20.0, 20.0, 50.0), (30.0, 25.0, 45.0), (40.0, 40.0, 40.0), (60.0, 60.0, 36.0),
        ],
    )
    def test_a_cover_plate_end_is_classed_by_both_thicknesses(self, t_c: float, t: float, category: float) -> None:
        # Table 8.5 detail 6 (p. 25): the columns are t_c < t and t_c ≥ t.
        assert _one(Joint.COVER_PLATE_END, {"t_c_mm": t_c, "t_mm": t}) == category

    def test_a_gusset_on_the_bound_between_two_rows_keeps_both(self) -> None:
        # Table 8.4 detail 4 (p. 24): r/ℓ ≥ 1/3 is 90 and 1/6 ≤ r/ℓ ≤ 1/3 is 71; the rows overlap.
        machined = {"radius_transition": True, "radius_machined_and_weld_ground": True}
        assert classify(Joint.GUSSET_ON_EDGE, {**machined, "r_mm": 100.0, "l_mm": 300.0}, basis=NOMINAL).categories() == (90, 71)
        assert classify(Joint.GUSSET_ON_EDGE, {**machined, "r_mm": 200.0, "l_mm": 1000.0}, basis=NOMINAL).categories() == (90, 71)
        assert _one(Joint.GUSSET_ON_EDGE, {**machined, "r_mm": 50.0, "l_mm": 500.0}) == 50
        assert _one(Joint.GUSSET_ON_EDGE, {"radius_transition": False}) == 40

    def test_a_transverse_attachment_longer_than_the_table_is_uncovered(self) -> None:
        result = classify(Joint.TRANSVERSE_ATTACHMENT, {"l_mm": 100.0}, basis=NOMINAL)
        assert result.candidates == ()
        assert result.uncovered is not None and "50 < l_mm ≤ 80" in result.uncovered

    def test_a_hot_spot_toe_steeper_than_60_degrees_is_uncovered(self) -> None:
        result = classify(Joint.HOT_SPOT_CRUCIFORM_K_BUTT, {"toe_angle_deg": 70.0}, basis=StressBasis.HOT_SPOT)
        assert result.candidates == () and result.uncovered is not None

    def test_a_ratio_over_a_zero_length_is_refused(self) -> None:
        with pytest.raises(ClassificationError, match="undefined"):
            classify(Joint.GUSSET_ON_EDGE, {"r_mm": 100.0, "l_mm": 0.0}, basis=NOMINAL)

    def test_a_band_prints_the_table_s_own_inequality(self) -> None:
        assert Band("l_mm", 50.0, False, 80.0, True).describe() == "50 < l_mm ≤ 80"


class TestTheFactsAreChecked:
    @pytest.mark.parametrize(
        "facts",
        [
            {"grund_flush": True},
            {"ground_flush": 1},
            {"t_mm": True},
            {"t_mm": -3.0},
            {"t_mm": math.nan},
            {"t_mm": "20"},
        ],
    )
    def test_a_fact_the_catalogue_cannot_read_is_refused(self, facts: dict[str, object]) -> None:
        with pytest.raises(ClassificationError):
            classify(Joint.PLATE_SPLICE, facts, basis=NOMINAL)  # type: ignore[arg-type]

    def test_every_fact_has_a_meaning(self) -> None:
        assert all(spec.meaning.strip() for spec in FACTS.values())


class TestTheBasisMustMatchTheTable:
    def test_a_nominal_table_is_refused_for_a_hot_spot_stress(self) -> None:
        with pytest.raises(ClassificationError, match="nominal stress"):
            classify(Joint.PLATE_SPLICE, basis=StressBasis.HOT_SPOT)

    def test_annex_b_is_refused_for_a_nominal_stress(self) -> None:
        with pytest.raises(ClassificationError, match="hot_spot stress"):
            classify(Joint.HOT_SPOT_BUTT, basis=NOMINAL)

    def test_a_notch_root_stress_has_no_table(self) -> None:
        with pytest.raises(ClassificationError):
            classify(Joint.PLATE_SPLICE, basis=StressBasis.NOTCH_ROOT)

    def test_annex_b_separates_its_butt_joints_on_grinding(self) -> None:
        # Table B.1 (p. 33): detail 1, ground flush, 112; detail 2, not ground flush, 100.
        hot = StressBasis.HOT_SPOT
        assert classify(Joint.HOT_SPOT_BUTT, {"ground_flush": True}, basis=hot).categories() == (112,)
        assert classify(Joint.HOT_SPOT_BUTT, {"ground_flush": False}, basis=hot).categories() == (100,)


class TestTheSizeFactor:
    @pytest.mark.parametrize(("t", "k_s"), [(10.0, 1.0), (25.0, 1.0), (50.0, 0.5**0.2), (100.0, 0.25**0.2)])
    def test_k_s_is_25_over_t_to_the_fifth_above_25_mm(self, t: float, k_s: float) -> None:
        assert thickness_size_factor(t) == pytest.approx(k_s, rel=1e-12)

    def test_a_thickness_that_is_not_positive_is_refused(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            thickness_size_factor(0.0)

    def test_detail_17_s_factor_is_one_for_equal_thin_plates_on_one_centreline(self) -> None:
        assert eccentric_step_size_factor(20.0, 20.0, 0.0) == 1.0

    def test_detail_17_s_factor_follows_the_page_s_formula(self) -> None:
        # Table 8.3 detail 17 (p. 23): k_s = (25/t1)^0.2 / (1 + 6e/t1 · t1^1.5/(t1^1.5 + t2^1.5)).
        assert eccentric_step_size_factor(20.0, 20.0, 2.0) == pytest.approx(1.0 / 1.3, rel=1e-12)
        t1, t2, e = 40.0, 60.0, 3.0
        expected = (25.0 / t1) ** 0.2 / (1.0 + 6.0 * e / t1 * t1**1.5 / (t1**1.5 + t2**1.5))
        assert eccentric_step_size_factor(t1, t2, e) == pytest.approx(expected, rel=1e-12)

    def test_detail_17_refuses_the_plates_the_wrong_way_round(self) -> None:
        with pytest.raises(ValueError, match="Swap"):
            eccentric_step_size_factor(30.0, 20.0, 1.0)
        with pytest.raises(ValueError, match="negative"):
            eccentric_step_size_factor(20.0, 30.0, -1.0)

    def test_a_candidate_s_detail_carries_the_reduced_category(self) -> None:
        facts = {"welded_both_sides": True, "backing_strip": False, "ground_flush": True}
        top = classify(Joint.PLATE_SPLICE, facts, basis=NOMINAL).candidates[0]
        detail = top.detail(chosen_by="responsible engineer, weld procedure WPS-7", t_mm=50.0)
        assert isinstance(detail, WeldDetail)
        assert detail.detail_category_mpa == pytest.approx(112.0 * 0.5**0.2, rel=1e-12)
        assert "k_s" in detail.source and "WPS-7" in detail.source
        assert "checked_by_ndt = yes" in detail.source

    def test_a_size_effect_row_needs_its_thickness_and_others_refuse_one(self) -> None:
        splice = classify(Joint.PLATE_SPLICE, basis=NOMINAL).candidates[0]
        with pytest.raises(ClassificationError, match="Give t_mm"):
            splice.detail(chosen_by="ME")
        rolled = next(c for c in classify(Joint.ROLLED_SECTION_BUTT, basis=NOMINAL).candidates if c.row.category_mpa == 63)
        with pytest.raises(ClassificationError, match="no size effect"):
            rolled.detail(chosen_by="ME", t_mm=40.0)
        assert rolled.detail(chosen_by="ME").detail_category_mpa == 63.0

    def test_detail_17_needs_both_thicknesses_and_the_eccentricity(self) -> None:
        (step,) = classify(Joint.THICKNESS_STEP_BUTT, basis=NOMINAL).candidates
        with pytest.raises(ClassificationError, match="t2_mm"):
            step.detail(chosen_by="ME", t_mm=20.0)
        detail = step.detail(chosen_by="ME", t_mm=20.0, t2_mm=20.0, e_mm=2.0)
        assert detail.detail_category_mpa == pytest.approx(71.0 / 1.3, rel=1e-12)

    def test_a_shear_row_gives_a_shear_detail_and_an_asterisk_is_stated(self) -> None:
        result = classify(Joint.CRUCIFORM_OR_TEE_ROOT, basis=NOMINAL)
        shear = result.conservative(StressKind.SHEAR)
        direct = result.conservative(StressKind.DIRECT)
        assert shear is not None and direct is not None
        assert isinstance(shear.detail(chosen_by="ME"), ShearDetail)
        assert "NOTE 3" in direct.detail(chosen_by="ME").source

    def test_choosing_a_row_needs_a_name(self) -> None:
        with pytest.raises(ValueError, match="source"):
            classify(Joint.TUBE_SOCKET_FILLET, basis=NOMINAL).candidates[0].detail(chosen_by=" ")


class TestTheShearCurve:
    def test_the_cut_off_is_0_457_of_the_category(self) -> None:
        detail = ShearDetail(detail_category_mpa=80.0, source="Table 8.5 detail 8")
        assert detail.cutoff_limit_mpa == pytest.approx(80.0 * (2.0 / 100.0) ** 0.2, rel=1e-12)
        assert detail.cutoff_limit_mpa / 80.0 == pytest.approx(0.457, abs=5e-4)

    def test_the_curve_gives_two_million_cycles_at_the_category_on_slope_five(self) -> None:
        curve = ShearDetail(detail_category_mpa=100.0, source="Table 8.5").sn_curve()
        assert curve.slope_k1 == curve.slope_k2 == 5.0
        life = curve.knee_cycles * (50.0 / curve.knee_amplitude_mpa) ** -curve.slope_k1
        assert life == pytest.approx(2.0e6, rel=1e-12)
        doubled = curve.knee_cycles * (25.0 / curve.knee_amplitude_mpa) ** -curve.slope_k1
        assert doubled / life == pytest.approx(32.0, rel=1e-12)

    def test_the_curve_carries_the_standard_s_survival_probability_as_a_reading(self) -> None:
        curve = ShearDetail(detail_category_mpa=80.0, source="Table 8.5").sn_curve()
        assert curve.failure_probability == EC3_FAILURE_PROBABILITY
        assert "read as applying to Δτc" in curve.source

    def test_a_shear_detail_needs_a_source_and_a_positive_category(self) -> None:
        with pytest.raises(ValueError, match="source"):
            ShearDetail(detail_category_mpa=80.0, source="")
        with pytest.raises(ValueError, match="positive"):
            ShearDetail(detail_category_mpa=0.0, source="x")


class TestThePartialFactor:
    @pytest.mark.parametrize(
        ("method", "consequence", "value"),
        [
            (AssessmentMethod.DAMAGE_TOLERANT, Consequence.LOW, 1.00),
            (AssessmentMethod.DAMAGE_TOLERANT, Consequence.HIGH, 1.15),
            (AssessmentMethod.SAFE_LIFE, Consequence.LOW, 1.15),
            (AssessmentMethod.SAFE_LIFE, Consequence.HIGH, 1.35),
        ],
    )
    def test_table_3_1_s_recommended_values(self, method: AssessmentMethod, consequence: Consequence, value: float) -> None:
        # Table 3.1 (p. 11).
        factor = recommended_gamma_mf(method, consequence)
        assert factor.value == value
        assert "recommended" in factor.source and "National Annex" in factor.source

    def test_a_partial_factor_needs_a_source_and_a_positive_value(self) -> None:
        with pytest.raises(ValueError, match="source"):
            PartialFactor(value=1.0, source="")
        with pytest.raises(ValueError, match="positive"):
            PartialFactor(value=0.0, source="x")


class TestTheCompressionRule:
    @pytest.mark.parametrize(
        ("maximum", "minimum", "effective"),
        [(100.0, -100.0, 160.0), (-20.0, -100.0, 48.0), (100.0, 20.0, 80.0), (0.0, -100.0, 60.0)],
    )
    def test_the_compressive_portion_counts_at_60_percent(self, maximum: float, minimum: float, effective: float) -> None:
        # §7.2.1(2) (p. 17) and Figure 7.4 (p. 18): Δσ = |σmax| + 0.6·|σmin| through zero.
        assert effective_range_mpa(maximum, minimum, state=WeldState.NON_WELDED) == pytest.approx(effective)

    def test_an_as_welded_detail_is_refused(self) -> None:
        with pytest.raises(ValueError, match="as-welded"):
            effective_range_mpa(100.0, -100.0, state=WeldState.AS_WELDED)

    def test_a_cycle_upside_down_is_refused(self) -> None:
        with pytest.raises(ValueError, match="below its minimum"):
            effective_range_mpa(-100.0, 100.0, state=WeldState.STRESS_RELIEVED)

    def test_a_collective_is_reduced_block_by_block_and_its_mean_is_spent(self) -> None:
        collective = Collective.from_blocks(
            [(100.0, 0.0, 1e5), (40.0, -60.0, 1e6)], basis=NOMINAL, source="counted"
        )
        reduced = effective_collective(collective, state=WeldState.STRESS_RELIEVED, source="PWHT record 12")
        assert [b.amplitude_mpa for b in reduced.blocks] == pytest.approx([80.0, 24.0])
        assert [b.cycles for b in reduced.blocks] == [1e5, 1e6]
        assert not reduced.has_mean_stress
        assert "§7.2.1" in reduced.source and "PWHT record 12" in reduced.source

    def test_an_unsigned_collective_is_refused(self) -> None:
        collective = Collective.from_blocks(
            [(100.0, 0.0, 1e5)], basis=NOMINAL, sign=SignConvention.UNSIGNED, source="von Mises"
        )
        with pytest.raises(ValueError, match="unsigned"):
            effective_collective(collective, state=WeldState.NON_WELDED, source="x")


class TestTheRangeLimit:
    def test_the_limits_are_one_and_a_half_yield_and_that_over_root_three(self) -> None:
        # §8(1), eq. (8.1) (p. 18).
        assert range_limit_mpa(355.0, StressKind.DIRECT) == pytest.approx(532.5)
        assert range_limit_mpa(355.0, StressKind.SHEAR) == pytest.approx(532.5 / math.sqrt(3.0))

    def test_a_range_at_the_limit_passes_and_above_it_fails(self) -> None:
        at = check_range_limit(532.5, yield_strength_mpa=355.0, stress=StressKind.DIRECT, source="frequent ψ1·Qk")
        above = check_range_limit(533.0, yield_strength_mpa=355.0, stress=StressKind.DIRECT, source="frequent ψ1·Qk")
        assert at.outcome is Outcome.PASSED and above.outcome is Outcome.FAILED
        assert "ψ1·Qk" in at.statement

    def test_a_range_with_no_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source"):
            check_range_limit(100.0, yield_strength_mpa=355.0, stress=StressKind.DIRECT, source="")


class TestTheVerification:
    gamma_ff = PartialFactor(value=1.0, source="EN 1993-2, stated by the engineer")
    gamma_mf = recommended_gamma_mf(AssessmentMethod.SAFE_LIFE, Consequence.HIGH)

    def _direct(self, range_mpa: float, category: float = 90.0) -> tuple[EquivalentRange, WeldDetail]:
        return (
            EquivalentRange(StressKind.DIRECT, range_mpa, "Δσ_E,2 from the load model"),
            WeldDetail(detail_category_mpa=category, source="Table 8.3 detail 5"),
        )

    def _shear(self, range_mpa: float, category: float = 80.0) -> tuple[EquivalentRange, ShearDetail]:
        return (
            EquivalentRange(StressKind.SHEAR, range_mpa, "Δτ_E,2 from the load model"),
            ShearDetail(detail_category_mpa=category, source="Table 8.5 detail 8"),
        )

    def test_the_direct_ratio_is_the_load_over_the_factored_category(self) -> None:
        # §8(2), eq. (8.2): γFf·Δσ_E,2 / (Δσ_C/γMf).
        result = verify(gamma_ff=self.gamma_ff, gamma_mf=self.gamma_mf, direct=self._direct(50.0))
        assert result.direct_ratio == pytest.approx(50.0 * 1.35 / 90.0)
        assert result.shear_ratio is None and result.interaction is None
        assert result.outcome is Outcome.PASSED

    def test_a_ratio_of_exactly_one_passes_and_above_one_fails(self) -> None:
        # "≤ 1,0". With γMf = 1.00 the ratio is exactly 1.0 in floating point.
        unity = recommended_gamma_mf(AssessmentMethod.DAMAGE_TOLERANT, Consequence.LOW)
        exact = verify(gamma_ff=self.gamma_ff, gamma_mf=unity, direct=self._direct(90.0))
        over = verify(gamma_ff=self.gamma_ff, gamma_mf=unity, direct=self._direct(90.5))
        assert exact.direct_ratio == 1.0
        assert exact.outcome is Outcome.PASSED and over.outcome is Outcome.FAILED

    def test_the_interaction_uses_cube_on_direct_and_fifth_power_on_shear(self) -> None:
        # §8(3), eq. (8.3).
        direct_ratio, shear_ratio = 0.95, 0.5
        result = verify(
            gamma_ff=self.gamma_ff,
            gamma_mf=self.gamma_mf,
            direct=self._direct(direct_ratio * 90.0 / 1.35),
            shear=self._shear(shear_ratio * 80.0 / 1.35),
        )
        assert result.interaction == pytest.approx(direct_ratio**3 + shear_ratio**5, rel=1e-12)
        assert result.outcome is Outcome.PASSED

    def test_two_ranges_that_each_pass_can_fail_together(self) -> None:
        result = verify(
            gamma_ff=self.gamma_ff,
            gamma_mf=self.gamma_mf,
            direct=self._direct(0.8 * 90.0 / 1.35),
            shear=self._shear(0.9 * 80.0 / 1.35),
        )
        assert result.direct_ratio is not None and result.direct_ratio < 1.0
        assert result.shear_ratio is not None and result.shear_ratio < 1.0
        assert result.interaction == pytest.approx(0.8**3 + 0.9**5)
        assert result.outcome is Outcome.FAILED

    def test_the_statements_carry_every_source(self) -> None:
        result = verify(gamma_ff=self.gamma_ff, gamma_mf=self.gamma_mf, direct=self._direct(50.0), shear=self._shear(30.0))
        text = " ".join(result.statements)
        assert "EN 1993-2, stated by the engineer" in text and "Table 3.1" in text
        assert "Δσ_E,2 from the load model" in text and "Tables 8.8 and 8.9" in text

    def test_nothing_to_verify_is_refused(self) -> None:
        with pytest.raises(ValueError, match="direct range, a shear range, or both"):
            verify(gamma_ff=self.gamma_ff, gamma_mf=self.gamma_mf)

    def test_a_range_passed_as_the_wrong_kind_is_refused(self) -> None:
        shear_range, _ = self._shear(30.0)
        with pytest.raises(ValueError, match="Pass it as `shear`"):
            verify(
                gamma_ff=self.gamma_ff,
                gamma_mf=self.gamma_mf,
                direct=(shear_range, WeldDetail(detail_category_mpa=90.0, source="x")),
            )
