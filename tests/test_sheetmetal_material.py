"""The sheet: its thickness, its minimum bend radius, and where that number came from.

Offline, no database, no kernel.
"""

from __future__ import annotations

import math

import pytest

from app.sheetmetal.errors import SheetMetalError
from app.sheetmetal.kfactor import MaterialFamily
from app.sheetmetal.material import (
    SHIPPED_GRADES,
    MinimumBendRadius,
    SheetMaterial,
    sheet_material,
)
from app.solve.materials import Source, SourceKind, Status


class TestShippedGrades:
    def test_every_shipped_grade_carries_a_sourced_minimum_and_the_grain_caveat(self) -> None:
        for grade in SHIPPED_GRADES:
            material = sheet_material(grade, thickness_mm=1.5)
            record = material.minimum_bend_radius
            assert record is not None, grade
            assert record.source.citation, grade
            assert record.status is Status.TYPICAL, grade
            assert "across the grain" in record.note, grade
            assert "Screening" in record.note, grade

    def test_the_limit_is_a_multiple_of_the_thickness_it_was_asked_for(self) -> None:
        thin = sheet_material("steel_mild_cr", thickness_mm=1.0)
        thick = sheet_material("steel_mild_cr", thickness_mm=4.0)
        assert thin.minimum_bend_radius_mm() == pytest.approx(1.0)
        assert thick.minimum_bend_radius_mm() == pytest.approx(4.0)

    def test_6061_t6_is_the_awkward_one_and_the_table_says_so(self) -> None:
        assert sheet_material(
            "aluminium_6061_t6", thickness_mm=2.0
        ).minimum_bend_radius_mm() == pytest.approx(6.0)
        assert sheet_material(
            "aluminium_1100_o", thickness_mm=2.0
        ).minimum_bend_radius_mm() == pytest.approx(1.0)

    def test_an_unknown_grade_is_refused_and_the_message_lists_what_there_is(self) -> None:
        with pytest.raises(SheetMetalError, match="not one of the grades") as caught:
            sheet_material("unobtainium", thickness_mm=2.0)
        assert "steel_mild_cr" in str(caught.value)
        assert "materials database" in str(caught.value)


class TestOuterFibreStrain:
    def test_the_worked_example(self) -> None:
        """(1-0.44)*2 / (3 + 0.44*2) = 1.12/3.88."""
        material = sheet_material("steel_mild_cr", thickness_mm=2.0)
        assert material.outer_fibre_strain(
            inside_radius_mm=3.0, k=0.44
        ) == pytest.approx(1.12 / 3.88, abs=1e-15)
        assert material.outer_fibre_strain(
            inside_radius_mm=3.0, k=0.44
        ) == pytest.approx(0.2886597938144330, abs=1e-12)

    def test_a_mid_plane_neutral_axis_gives_the_textbook_form(self) -> None:
        """With K = 0.5 the strain reduces to t / (2r + t)."""
        material = sheet_material("steel_mild_cr", thickness_mm=2.0)
        assert material.outer_fibre_strain(inside_radius_mm=3.0, k=0.5) == pytest.approx(
            2.0 / (2.0 * 3.0 + 2.0), abs=1e-15
        )

    def test_opening_the_radius_reduces_the_strain(self) -> None:
        material = sheet_material("steel_mild_cr", thickness_mm=2.0)
        tight = material.outer_fibre_strain(inside_radius_mm=1.0, k=0.44)
        open_ = material.outer_fibre_strain(inside_radius_mm=10.0, k=0.44)
        assert tight > open_
        assert open_ < 0.11


class TestGuards:
    def test_a_negative_minimum_radius_factor_is_refused(self) -> None:
        with pytest.raises(SheetMetalError, match="meaningless"):
            MinimumBendRadius(
                factor=-1.0,
                status=Status.TYPICAL,
                source=Source(citation="somewhere", kind=SourceKind.TEXTBOOK),
            )

    def test_an_estimated_minimum_radius_must_say_how_it_was_estimated(self) -> None:
        with pytest.raises(SheetMetalError, match="how it was estimated"):
            MinimumBendRadius(
                factor=1.0,
                status=Status.ESTIMATED,
                source=Source(citation="by analogy", kind=SourceKind.DERIVED),
            )

    def test_a_zero_factor_is_allowed_because_some_grades_fold_flat(self) -> None:
        record = MinimumBendRadius(
            factor=0.0,
            status=Status.TYPICAL,
            source=Source(citation="dead soft foil", kind=SourceKind.DATASHEET),
        )
        assert record.radius_mm(2.0) == 0.0

    @pytest.mark.parametrize("bad", [0.0, -1.0, math.nan])
    def test_a_sheet_with_no_thickness_is_refused(self, bad: float) -> None:
        with pytest.raises(SheetMetalError, match="not a sheet"):
            SheetMaterial(
                name="something", family=MaterialFamily.STEEL, thickness_mm=bad
            )

    def test_a_sheet_with_no_name_is_refused(self) -> None:
        with pytest.raises(SheetMetalError, match="needs a name"):
            SheetMaterial(name="  ", family=MaterialFamily.STEEL, thickness_mm=2.0)

    def test_a_material_with_no_minimum_reports_none_rather_than_a_default(self) -> None:
        material = SheetMaterial(
            name="unspecified", family=MaterialFamily.STEEL, thickness_mm=2.0
        )
        assert material.minimum_bend_radius_mm() is None
        assert material.to_dict()["minimum_bend_radius"] is None
