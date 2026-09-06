"""The K-factor: the two traditions, and the refusals that keep a bare number out.

Every value here is recomputed in the test from the published constants rather
than pinned from a run:

* Machinery's Handbook prints `0.0078*T` and `0.0087*T` in its bend-allowance
  formula, where the radius term is `0.01743*R = (pi/180)*R`. So the K it
  implies is `0.0078/(pi/180) = 0.446907` below `R = 2T` and
  `0.0087/(pi/180) = 0.498473` at or above it.
* DIN 6935's unfolding factor is `k = 0.65 + 0.5*log10(r/t)` up to `r/t = 5` and
  `k = 1` beyond, and `K = k/2`.

The most informative test in the file is
`test_the_din_formula_is_continuous_into_its_own_plateau`: the two halves of the
published piecewise definition meet to 2.6e-4, which is a property of the
constants 0.65 and 0.5 and not of this code. A mistyped constant breaks it and
no recorded output would.

Offline, no database, no kernel.
"""

from __future__ import annotations

import math

import pytest

from app.sheetmetal.errors import BendError
from app.sheetmetal.kfactor import (
    DEGREE,
    DIN_MINIMUM_R_OVER_T,
    DIN_PLATEAU_R_OVER_T,
    MACHINERYS_HANDBOOK,
    Band,
    Basis,
    KFactor,
    KFactorTable,
    MaterialFamily,
    assumed,
    din6935,
    machinerys_handbook,
    measured,
    unstated,
)
from app.solve.materials import Source, SourceKind, Status


def _din_k(ratio: float) -> float:
    """DIN 6935's own definition, written out here so the module cannot define it."""
    return 1.0 if ratio >= 5.0 else 0.65 + 0.5 * math.log10(ratio)


class TestDin6935:
    def test_k_is_half_the_unfolding_factor(self) -> None:
        k = din6935(inside_radius_mm=3.0, thickness_mm=2.0)
        assert k.value == pytest.approx(_din_k(1.5) / 2.0, abs=1e-15)
        assert k.value == pytest.approx(0.3690228147639203, abs=1e-12)
        assert k.r_over_t == pytest.approx(1.5)

    def test_at_r_equals_t_the_factor_is_the_bare_constant(self) -> None:
        # log10(1) = 0, so k = 0.65 exactly and K = 0.325 exactly.
        k = din6935(inside_radius_mm=1.5, thickness_mm=1.5)
        assert k.value == pytest.approx(0.325, abs=1e-15)

    def test_above_the_plateau_the_neutral_axis_is_at_the_mid_plane(self) -> None:
        k = din6935(inside_radius_mm=20.0, thickness_mm=2.0)
        assert k.value == 0.5

    def test_the_din_formula_is_continuous_into_its_own_plateau(self) -> None:
        """0.65 + 0.5*log10(5) = 0.99948, against a plateau of exactly 1.

        The published constants were chosen so the two halves meet. If either is
        mistyped they stop meeting, which is what this asserts.
        """
        just_below = din6935(
            inside_radius_mm=DIN_PLATEAU_R_OVER_T - 1e-9, thickness_mm=1.0
        )
        at_plateau = din6935(inside_radius_mm=DIN_PLATEAU_R_OVER_T, thickness_mm=1.0)
        assert at_plateau.value == 0.5
        assert abs(at_plateau.value - just_below.value) < 3e-4
        assert just_below.value == pytest.approx(0.49974250108400467, abs=1e-9)

    def test_on_steel_it_is_a_specified_value_with_a_stated_basis(self) -> None:
        k = din6935(inside_radius_mm=3.0, thickness_mm=2.0, family=MaterialFamily.STEEL)
        assert k.status is Status.SPECIFIED
        assert k.basis is Basis.STANDARD_FORMULA
        assert k.has_stated_basis
        assert "DIN 6935" in k.source.citation

    def test_off_steel_it_is_estimated_and_names_the_analogy(self) -> None:
        """ESTIMATED with a basis: the number has a source and is applied by analogy.

        `has_stated_basis` stays true — this is not an assumed K — but the note
        says what was assumed about it, which is the distinction
        `app.solve.materials.Status` draws.
        """
        k = din6935(
            inside_radius_mm=3.0, thickness_mm=2.0, family=MaterialFamily.ALUMINIUM
        )
        assert k.status is Status.ESTIMATED
        assert k.has_stated_basis
        assert "aluminium" in k.note
        assert "analogy" in k.note

    def test_below_the_domain_it_refuses_rather_than_clamping(self) -> None:
        with pytest.raises(BendError, match="stops describing a bend"):
            din6935(inside_radius_mm=DIN_MINIMUM_R_OVER_T / 2.0, thickness_mm=1.0)

    def test_a_zero_radius_is_refused(self) -> None:
        with pytest.raises(BendError):
            din6935(inside_radius_mm=0.0, thickness_mm=1.0)


class TestMachinerysHandbook:
    def test_the_tight_band_k_is_the_published_coefficient_over_degrees(self) -> None:
        k = machinerys_handbook(inside_radius_mm=3.0, thickness_mm=2.0)
        assert k.value == pytest.approx(0.0078 / DEGREE, abs=1e-15)
        assert k.value == pytest.approx(0.446907, abs=1e-6)
        assert k.basis is Basis.TABLE
        assert k.status is Status.TYPICAL

    def test_the_open_band_k_is_the_other_coefficient(self) -> None:
        k = machinerys_handbook(inside_radius_mm=4.0, thickness_mm=2.0)
        assert k.value == pytest.approx(0.0087 / DEGREE, abs=1e-15)
        assert k.value == pytest.approx(0.498473, abs=1e-6)

    def test_the_band_boundary_is_half_open_at_two_thicknesses(self) -> None:
        below = machinerys_handbook(inside_radius_mm=3.999999, thickness_mm=2.0)
        at = machinerys_handbook(inside_radius_mm=4.0, thickness_mm=2.0)
        assert below.value == pytest.approx(0.0078 / DEGREE)
        assert at.value == pytest.approx(0.0087 / DEGREE)

    def test_the_open_band_value_stays_under_the_physical_cap(self) -> None:
        assert 0.0087 / DEGREE < 0.5

    def test_the_table_carries_its_own_citation(self) -> None:
        assert MACHINERYS_HANDBOOK.source.kind is SourceKind.TEXTBOOK
        assert "0.0078" in MACHINERYS_HANDBOOK.source.citation
        assert "0.0087" in MACHINERYS_HANDBOOK.source.citation

    def test_a_family_the_table_was_not_written_for_is_refused(self) -> None:
        with pytest.raises(BendError, match="not a conservative answer"):
            machinerys_handbook(
                inside_radius_mm=3.0, thickness_mm=2.0, family=MaterialFamily.TITANIUM
            )


class TestTheTwoTraditionsDisagree:
    """The ANSI/DIN distinction is real, and this is how much of it there is."""

    def test_they_give_different_k_at_the_same_geometry(self) -> None:
        ansi = machinerys_handbook(inside_radius_mm=3.0, thickness_mm=2.0)
        din = din6935(inside_radius_mm=3.0, thickness_mm=2.0)
        assert ansi.value > din.value
        assert ansi.value / din.value == pytest.approx(1.211, abs=5e-3)

    def test_the_disagreement_moves_a_real_blank(self) -> None:
        """At 90 degrees on 2 mm sheet the two allowances differ by 0.24 mm."""
        ansi = machinerys_handbook(inside_radius_mm=3.0, thickness_mm=2.0).value
        din = din6935(inside_radius_mm=3.0, thickness_mm=2.0).value
        difference = (math.pi / 2.0) * 2.0 * (ansi - din)
        assert difference == pytest.approx(0.2447, abs=1e-3)


class TestRefusals:
    """Guards, each verified by breaking the thing it guards."""

    def test_k_above_a_half_is_refused_and_the_message_offers_the_y_factor(self) -> None:
        with pytest.raises(BendError, match="Y-factor"):
            assumed(0.7, why="a Y-factor typed into the K field")

    def test_k_at_a_half_is_allowed_because_that_is_the_mid_plane(self) -> None:
        assert assumed(0.5, why="theoretical upper bound").value == 0.5

    @pytest.mark.parametrize("bad", [0.0, -0.3, math.nan, math.inf])
    def test_a_non_positive_or_non_finite_k_is_refused(self, bad: float) -> None:
        with pytest.raises(BendError):
            assumed(bad, why="broken")

    def test_an_assumed_k_needs_a_reason(self) -> None:
        with pytest.raises(BendError, match="needs a reason"):
            assumed(0.42, why="   ")

    def test_an_estimated_k_with_no_note_is_refused(self) -> None:
        with pytest.raises(BendError, match="how it was estimated"):
            KFactor(
                value=0.42,
                basis=Basis.TABLE,
                source=Source(citation="somewhere", kind=SourceKind.TEXTBOOK),
                status=Status.ESTIMATED,
            )

    def test_an_assumed_k_claiming_a_status_it_does_not_have_is_refused(self) -> None:
        with pytest.raises(BendError, match="no source"):
            KFactor(
                value=0.42,
                basis=Basis.ASSUMED,
                source=Source(citation="nowhere", kind=SourceKind.DERIVED),
                status=Status.SPECIFIED,
            )

    def test_a_source_with_no_citation_is_refused_upstream(self) -> None:
        """The provenance record is `app.solve.materials.Source`, not a copy of it."""
        with pytest.raises(ValueError, match="needs a citation"):
            Source(citation="", kind=SourceKind.TEXTBOOK)

    def test_a_table_with_a_gap_between_its_bands_is_refused(self) -> None:
        with pytest.raises(BendError, match="gap or an overlap"):
            KFactorTable(
                name="gappy",
                source=Source(citation="nowhere in particular", kind=SourceKind.TEXTBOOK),
                status=Status.TYPICAL,
                bands=(Band(0.0, 1.0, 0.4), Band(2.0, 3.0, 0.45)),
                families=frozenset({MaterialFamily.STEEL}),
            )

    def test_a_table_with_overlapping_bands_is_refused(self) -> None:
        with pytest.raises(BendError, match="gap or an overlap"):
            KFactorTable(
                name="overlapping",
                source=Source(citation="nowhere in particular", kind=SourceKind.TEXTBOOK),
                status=Status.TYPICAL,
                bands=(Band(0.0, 2.0, 0.4), Band(1.0, 3.0, 0.45)),
                families=frozenset({MaterialFamily.STEEL}),
            )

    def test_a_table_with_no_bands_is_refused(self) -> None:
        with pytest.raises(BendError, match="no bands"):
            KFactorTable(
                name="empty",
                source=Source(citation="nowhere in particular", kind=SourceKind.TEXTBOOK),
                status=Status.TYPICAL,
                bands=(),
                families=frozenset({MaterialFamily.STEEL}),
            )

    def test_a_ratio_outside_every_band_is_refused(self) -> None:
        table = KFactorTable(
            name="narrow",
            source=Source(citation="a narrow table", kind=SourceKind.TEXTBOOK),
            status=Status.TYPICAL,
            bands=(Band(1.0, 2.0, 0.42),),
            families=frozenset({MaterialFamily.STEEL}),
        )
        assert table.k_for(
            inside_radius_mm=1.5, thickness_mm=1.0, family=MaterialFamily.STEEL
        ).value == pytest.approx(0.42)
        with pytest.raises(BendError, match="does not cover"):
            table.k_for(
                inside_radius_mm=5.0, thickness_mm=1.0, family=MaterialFamily.STEEL
            )


class TestBasisIsVisible:
    def test_an_assumed_k_says_it_has_no_basis(self) -> None:
        k = assumed(0.44, why="round number for a worked example")
        assert not k.has_stated_basis
        assert k.basis is Basis.ASSUMED
        assert k.status is Status.ESTIMATED
        assert "no published basis" in k.source.citation
        assert k.to_dict()["has_stated_basis"] is False

    def test_a_measured_k_is_a_design_basis(self) -> None:
        k = measured(
            0.41,
            source=Source(
                citation="test bend, 2 mm DC01 on the 16 mm V, 2026-09-06",
                kind=SourceKind.TEST_REPORT,
            ),
            r_over_t=1.5,
        )
        assert k.basis is Basis.TEST_BEND
        assert k.status.is_design_basis
        assert k.has_stated_basis

    def test_unstated_picks_out_only_the_ones_with_no_basis(self) -> None:
        good = din6935(inside_radius_mm=3.0, thickness_mm=2.0)
        bad = assumed(0.44, why="a guess")
        assert unstated([good, bad]) == (bad,)
        assert unstated([good]) == ()

    def test_str_names_the_source(self) -> None:
        k = din6935(inside_radius_mm=3.0, thickness_mm=2.0)
        assert "DIN 6935" in str(k)
        assert "r/t=1.5" in str(k)
