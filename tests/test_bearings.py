"""Bearing selection (E12.4).

Two halves, and they are tested differently on purpose. The **arithmetic** is
ISO 281 and is checked against the standard's own definitions and against a
worked example computed by hand here — the same treatment the solver gets
against closed-form solutions. The **selection** is a policy, and what is
asserted about it is mostly what it refuses.

No database anywhere: `app/parts/` holds no session and this runs offline in
milliseconds, like the rest of the engineering layer.
"""

from __future__ import annotations

import pytest

from app.parts.bearings import (
    CATALOGUE,
    SHIPPED_BEARINGS,
    Bearing,
    BearingCatalogue,
    BearingError,
    BearingKind,
    Duty,
    Refusal,
    Selection,
    describe,
    equivalent_dynamic_load,
    rating_life_hours,
    rating_life_revolutions,
    required_dynamic_rating_n,
    select,
    static_safety_factor,
)
from app.solve.materials import Property, Source, SourceKind, Status

_SKF = Source(
    citation="a manufacturer catalogue, transcribed for this test",
    kind=SourceKind.DATASHEET,
)


def _rated(designation: str, bore: float, outer: float, width: float, c: float, c0: float) -> Bearing:
    return Bearing(
        designation=designation,
        kind=BearingKind.BALL,
        bore_mm=bore,
        outer_diameter_mm=outer,
        width_mm=width,
        dynamic_rating=Property(
            name="basic_dynamic_load_rating_n", value=c, status=Status.SPECIFIED, source=_SKF
        ),
        static_rating=Property(
            name="basic_static_load_rating_n", value=c0, status=Status.SPECIFIED, source=_SKF
        ),
    )


class TestTheStandardsArithmetic:
    def test_the_life_exponent_is_three_for_a_ball_and_ten_thirds_for_a_roller(self) -> None:
        # A property of the contact -- point for a ball, line for a roller. Using
        # 3 for a roller overstates its life by about 40% at a typical load
        # ratio, which is why it is not a parameter anybody can pass.
        assert BearingKind.BALL.life_exponent == 3.0
        assert BearingKind.ROLLER.life_exponent == pytest.approx(10 / 3)

    def test_l10_is_the_load_ratio_cubed(self) -> None:
        # C/P = 3.5, so L10 = 3.5^3 = 42.875 million revolutions.
        assert rating_life_revolutions(
            dynamic_rating_n=14_000, equivalent_load_n=4_000, kind=BearingKind.BALL
        ) == pytest.approx(42.875)

    def test_the_worked_example_in_hours(self) -> None:
        # 42.875e6 revolutions at 1500 rpm is 42.875e6 / (60 * 1500) = 476.4 h.
        # Computed here from the definition rather than recalled.
        assert rating_life_hours(
            dynamic_rating_n=14_000,
            equivalent_load_n=4_000,
            speed_rpm=1_500,
            kind=BearingKind.BALL,
        ) == pytest.approx(42.875e6 / (60 * 1500))

    def test_halving_the_load_multiplies_a_ball_bearings_life_by_eight(self) -> None:
        # The cube law, which is the single most useful thing to know about a
        # bearing and the reason a small load reduction is worth chasing.
        heavy = rating_life_hours(
            dynamic_rating_n=10_000, equivalent_load_n=2_000, speed_rpm=1_000,
            kind=BearingKind.BALL,
        )
        light = rating_life_hours(
            dynamic_rating_n=10_000, equivalent_load_n=1_000, speed_rpm=1_000,
            kind=BearingKind.BALL,
        )
        assert light == pytest.approx(heavy * 8)

    def test_required_rating_inverts_the_life_calculation_exactly(self) -> None:
        # The direction an engineer actually works in, and it must round-trip or
        # a selection is chasing a number that does not mean what it says.
        needed = required_dynamic_rating_n(
            equivalent_load_n=4_000, speed_rpm=1_500, life_hours=20_000,
            kind=BearingKind.BALL,
        )
        assert rating_life_hours(
            dynamic_rating_n=needed, equivalent_load_n=4_000, speed_rpm=1_500,
            kind=BearingKind.BALL,
        ) == pytest.approx(20_000)

    def test_it_round_trips_for_a_roller_too(self) -> None:
        needed = required_dynamic_rating_n(
            equivalent_load_n=9_000, speed_rpm=600, life_hours=40_000,
            kind=BearingKind.ROLLER,
        )
        assert rating_life_hours(
            dynamic_rating_n=needed, equivalent_load_n=9_000, speed_rpm=600,
            kind=BearingKind.ROLLER,
        ) == pytest.approx(40_000)

    def test_a_pure_radial_load_is_the_radial_load(self) -> None:
        assert equivalent_dynamic_load(
            radial_n=5_000, axial_n=0, x_factor=1.0, y_factor=0.0
        ) == 5_000

    def test_the_x_and_y_factors_are_never_guessed(self) -> None:
        # They come from a table per bearing series and depend on `e`, which
        # depends on f0*Fa/C0. A default here would be a number from a different
        # bearing.
        assert equivalent_dynamic_load(
            radial_n=1_000, axial_n=2_000, x_factor=0.56, y_factor=1.6
        ) == pytest.approx(0.56 * 1000 + 1.6 * 2000)

    def test_the_static_check_is_separate_from_life(self) -> None:
        # Fatigue under rotation and permanent indentation under a standing load
        # are different failures; a bearing can pass one and fail the other.
        assert static_safety_factor(static_rating_n=8_000, static_load_n=4_000) == 2.0


class TestTheArithmeticRefusesNonsense:
    def test_a_stationary_bearing_has_no_rating_life(self) -> None:
        with pytest.raises(BearingError, match="no rating life"):
            rating_life_hours(
                dynamic_rating_n=10_000, equivalent_load_n=1_000, speed_rpm=0,
                kind=BearingKind.BALL,
            )

    def test_a_bearing_carrying_nothing_has_no_fatigue_life(self) -> None:
        with pytest.raises(BearingError, match="does not have a fatigue life"):
            equivalent_dynamic_load(radial_n=0, axial_n=0, x_factor=1.0, y_factor=0.0)

    def test_a_negative_load_is_refused_rather_than_squared_away(self) -> None:
        with pytest.raises(BearingError, match="magnitudes"):
            equivalent_dynamic_load(radial_n=-100, axial_n=0, x_factor=1.0, y_factor=0.0)

    def test_a_duty_with_no_speed_is_refused_at_construction(self) -> None:
        with pytest.raises(BearingError, match="needs a speed"):
            Duty(radial_n=1000, speed_rpm=0, required_life_hours=10_000)


class TestTheShippedCatalogue:
    def test_it_carries_boundary_dimensions_and_no_ratings(self) -> None:
        # ISO 15 boundary dimensions are a standard and are the same for every
        # maker, which is what makes them safe to ship. C and C0 are not.
        assert len(SHIPPED_BEARINGS) >= 20
        for bearing in SHIPPED_BEARINGS:
            assert bearing.source is not None
            assert bearing.dynamic_rating is None
            assert bearing.static_rating is None
            assert not bearing.is_selectable

    def test_every_shipped_bearing_names_what_it_is_missing(self) -> None:
        for bearing in SHIPPED_BEARINGS:
            assert bearing.missing == (
                "basic dynamic load rating C",
                "basic static load rating C0",
            )

    def test_a_bearing_whose_outside_is_inside_its_bore_is_refused(self) -> None:
        # A transcription error that would sort correctly in a catalogue search
        # while being nonsense.
        with pytest.raises(ValueError, match="outer diameter must exceed the bore"):
            Bearing(
                designation="bad",
                kind=BearingKind.BALL,
                bore_mm=30.0,
                outer_diameter_mm=20.0,
                width_mm=10.0,
            )

    def test_fitting_sorts_by_outside_diameter(self) -> None:
        # Given two that fit the shaft and both reach the life, the smaller one
        # is the answer unless something else says otherwise.
        fits = CATALOGUE.fitting(bore_mm=25.0)
        assert [b.designation for b in fits] == ["6005", "6205", "6305"]

    def test_an_unknown_designation_is_refused_by_name(self) -> None:
        with pytest.raises(BearingError, match="No bearing"):
            CATALOGUE.get("9999")


class TestSelection:
    def _catalogue(self) -> BearingCatalogue:
        return BearingCatalogue.of(
            [
                _rated("6005", 25.0, 47.0, 12.0, c=11_900, c0=6_550),
                _rated("6205", 25.0, 52.0, 15.0, c=14_800, c0=7_800),
                _rated("6305", 25.0, 62.0, 17.0, c=23_400, c0=11_600),
            ]
        )

    def test_the_smallest_bearing_that_reaches_the_life_is_chosen(self) -> None:
        # Deliberately lands on the *middle* bearing, so the test proves real
        # selection rather than "always the biggest" or "always the first".
        # 20,000 h at 1,500 rpm is 1,800 million revolutions, so a 1,200 N load
        # needs C >= 1200 * 1800^(1/3) = 14,597 N: past the 6005's 11,900 and
        # inside the 6205's 14,800.
        duty = Duty(radial_n=1_200, speed_rpm=1_500, required_life_hours=20_000)

        chosen = select(duty, bore_mm=25.0, catalogue=self._catalogue())

        assert isinstance(chosen, Selection)
        assert chosen.bearing.designation == "6205"
        assert chosen.life_hours >= duty.required_life_hours
        assert chosen.required_dynamic_rating_n == pytest.approx(14_597, rel=1e-3)

    def test_a_duty_just_past_the_largest_bearing_is_refused_rather_than_rounded(
        self,
    ) -> None:
        # 2,000 N needs 24,329 N and the largest entry has 23,400. Four per cent
        # short is still short, and a selection engine that rounded here would
        # be the most dangerous kind of helpful.
        duty = Duty(radial_n=2_000, speed_rpm=1_500, required_life_hours=20_000)

        refused = select(duty, bore_mm=25.0, catalogue=self._catalogue())

        assert isinstance(refused, Refusal)

    def test_a_lighter_duty_takes_the_smaller_bearing(self) -> None:
        duty = Duty(radial_n=500, speed_rpm=1_500, required_life_hours=20_000)

        chosen = select(duty, bore_mm=25.0, catalogue=self._catalogue())

        assert isinstance(chosen, Selection)
        assert chosen.bearing.designation == "6005"

    def test_nothing_is_ever_chosen_without_a_sourced_rating(self) -> None:
        # The rule this module exists to keep: a selection computed from an
        # invented rating looks exactly like engineering and is not.
        duty = Duty(radial_n=500, speed_rpm=1_500, required_life_hours=1_000)

        refused = select(duty, bore_mm=25.0, catalogue=CATALOGUE)

        assert isinstance(refused, Refusal)
        assert "missing its load ratings" in refused.reason
        assert refused.unselectable  # and it names them

    def test_a_shaft_no_bearing_fits_is_a_different_refusal(self) -> None:
        # "Nothing fits" and "everything that fits has no rating" are different
        # problems with different fixes; a bare None would make them the same.
        duty = Duty(radial_n=500, speed_rpm=1_500, required_life_hours=1_000)

        refused = select(duty, bore_mm=23.5, catalogue=self._catalogue())

        assert isinstance(refused, Refusal)
        assert "23.5 mm bore" in refused.reason

    def test_a_duty_nothing_can_meet_says_what_rating_would_be_needed(self) -> None:
        duty = Duty(radial_n=20_000, speed_rpm=3_000, required_life_hours=50_000)

        refused = select(duty, bore_mm=25.0, catalogue=self._catalogue())

        assert isinstance(refused, Refusal)
        assert "dynamic rating it would need" in refused.reason
        # And it offers the three things that can actually change.
        assert "larger shaft" in refused.reason

    def test_a_bearing_that_passes_on_life_and_fails_the_static_check_is_skipped(
        self,
    ) -> None:
        # A selection that fails a stated check is not a selection.
        duty = Duty(
            radial_n=2_000,
            speed_rpm=1_500,
            required_life_hours=1_000,
            minimum_static_safety=5.0,
        )

        chosen = select(duty, bore_mm=25.0, catalogue=self._catalogue())

        assert isinstance(chosen, Selection)
        assert chosen.static_safety >= 5.0
        assert chosen.bearing.designation == "6305"

    def test_every_selection_carries_its_caveats(self) -> None:
        # None of this is checked here, and a selection that stayed silent would
        # read as a full sign-off.
        duty = Duty(radial_n=500, speed_rpm=1_500, required_life_hours=1_000)

        chosen = select(duty, bore_mm=25.0, catalogue=self._catalogue())

        assert isinstance(chosen, Selection)
        assert any("90%" in caveat for caveat in chosen.caveats)
        assert any("a_ISO" in caveat for caveat in chosen.caveats)
        assert any("Lubrication" in caveat for caveat in chosen.caveats)

    def test_the_description_is_something_an_engineer_can_paste_in_a_report(
        self,
    ) -> None:
        duty = Duty(radial_n=500, speed_rpm=1_500, required_life_hours=1_000)
        chosen = select(duty, bore_mm=25.0, catalogue=self._catalogue())
        assert isinstance(chosen, Selection)

        line = describe(chosen)

        assert "6005" in line
        assert "L10h" in line
        assert "s0" in line
