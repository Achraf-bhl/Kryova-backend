"""The three manufacturability limits, each checked against arithmetic written out here.

On 2 mm mild steel with a 3 mm inside radius and K = 0.44:

* minimum bend radius = 1.0 x t = 2.0 mm, so R3 passes and R1 fails;
* outer fibre strain = (1-0.44)*2 / (3 + 0.44*2) = 1.12/3.88 = 28.866%;
* default die opening = 8t = 16 mm, so the minimum outside flange is
  16/2 + 3 + 2 = 13.0 mm;
* a hole's edge must clear a bend tangent by 2t = 4.0 mm.

Every one of those is recomputed in the test that uses it. Offline, no database,
no kernel.
"""

from __future__ import annotations

import pytest

from app.design.assertions import Outcome
from app.sheetmetal.bend import Bend, BendDirection, LengthConvention
from app.sheetmetal.errors import FormabilityError
from app.sheetmetal.formability import (
    AIR_BEND_DIE_RATIO,
    HOLE_EDGE_TO_TANGENT_FACTOR,
    air_bend_die_opening_mm,
    check_part,
    minimum_flange_mm,
)
from app.sheetmetal.kfactor import MaterialFamily, assumed, din6935
from app.sheetmetal.material import SheetMaterial, sheet_material
from app.sheetmetal.unfold import Edge, Flange, Hole, Joint, SheetMetalPart

THICKNESS = 2.0
RADIUS = 3.0
K44 = assumed(0.44, why="the phase brief's worked example, pinned by hand arithmetic")


def steel() -> SheetMaterial:
    return sheet_material("steel_mild_cr", thickness_mm=THICKNESS)


def bracket(
    *,
    material: SheetMaterial | None = None,
    inside_radius_mm: float = RADIUS,
    flange_length: float = 30.0,
    holes: tuple[Hole, ...] = (),
    k: object = None,
) -> SheetMetalPart:
    bend = Bend(
        angle_deg=90.0,
        inside_radius_mm=inside_radius_mm,
        direction=BendDirection.UP,
        k=K44 if k is None else k,  # type: ignore[arg-type]
        name="b1",
    )
    flange = Flange(name="flange", length_mm=flange_length, holes=holes)
    base = Flange(
        name="base",
        length_mm=40.0,
        width_mm=100.0,
        joints=(Joint(edge=Edge.FAR, bend=bend, flange=flange),),
    )
    return SheetMetalPart(
        name="bracket",
        material=material or steel(),
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
        root=base,
    )


def finding(report: object, check: str, subject: str | None = None) -> object:
    matches = [
        f
        for f in report.findings  # type: ignore[attr-defined]
        if f.check == check and (subject is None or f.subject == subject)
    ]
    assert matches, f"no {check!r} finding for {subject!r}"
    assert len(matches) == 1, [f.subject for f in matches]
    return matches[0]


class TestTheDefaultsAreArithmetic:
    def test_the_die_opening_is_eight_thicknesses(self) -> None:
        assert AIR_BEND_DIE_RATIO == 8.0
        assert air_bend_die_opening_mm(THICKNESS) == 16.0

    def test_the_minimum_flange_is_half_the_die_plus_the_radius_and_thickness(self) -> None:
        assert minimum_flange_mm(
            thickness_mm=THICKNESS, inside_radius_mm=RADIUS, die_opening_mm=16.0
        ) == pytest.approx(13.0)

    def test_the_hole_clearance_is_two_thicknesses(self) -> None:
        assert HOLE_EDGE_TO_TANGENT_FACTOR * THICKNESS == 4.0


class TestMinimumBendRadius:
    def test_a_radius_above_the_material_minimum_passes_and_reports_the_strain(self) -> None:
        result = finding(check_part(bracket()), "minimum bend radius")
        assert result.outcome is Outcome.PASSED  # type: ignore[attr-defined]
        assert result.limit == pytest.approx(2.0)  # type: ignore[attr-defined]
        assert result.measured == pytest.approx(3.0)  # type: ignore[attr-defined]
        # (1-0.44)*2 / (3 + 0.44*2) = 1.12/3.88 = 0.288659...
        assert "28.9%" in result.message  # type: ignore[attr-defined]

    def test_a_radius_below_it_fails_and_says_by_how_much(self) -> None:
        report = check_part(bracket(inside_radius_mm=1.0))
        result = finding(report, "minimum bend radius")
        assert result.outcome is Outcome.FAILED  # type: ignore[attr-defined]
        assert result.margin == pytest.approx(-1.0)  # type: ignore[attr-defined]
        assert "cracks" in result.message  # type: ignore[attr-defined]
        assert not report.ok

    def test_the_boundary_is_inclusive(self) -> None:
        result = finding(check_part(bracket(inside_radius_mm=2.0)), "minimum bend radius")
        assert result.outcome is Outcome.PASSED  # type: ignore[attr-defined]

    def test_a_harder_temper_moves_the_limit(self) -> None:
        """6061-T6 needs 3t, so the same R3 bend that passes on mild steel fails."""
        t6 = sheet_material("aluminium_6061_t6", thickness_mm=THICKNESS)
        assert t6.minimum_bend_radius_mm() == pytest.approx(6.0)
        result = finding(check_part(bracket(material=t6)), "minimum bend radius")
        assert result.outcome is Outcome.FAILED  # type: ignore[attr-defined]

    def test_a_material_with_no_stated_minimum_is_unmeasured_not_passed(self) -> None:
        unknown = SheetMaterial(
            name="something off a shelf",
            family=MaterialFamily.STEEL,
            thickness_mm=THICKNESS,
        )
        report = check_part(bracket(material=unknown))
        result = finding(report, "minimum bend radius")
        assert result.outcome is Outcome.UNMEASURED  # type: ignore[attr-defined]
        assert "no minimum bend radius on record" in result.message  # type: ignore[attr-defined]
        assert not result.ok  # type: ignore[attr-defined]
        assert report.ok  # nothing failed ...
        assert not report.complete  # ... and the part has not been assessed

    def test_the_finding_carries_the_material_s_own_source(self) -> None:
        result = finding(check_part(bracket()), "minimum bend radius")
        assert "minimum inside bend radius" in result.source.citation  # type: ignore[attr-defined]


class TestMinimumFlangeLength:
    def test_a_long_enough_flange_passes(self) -> None:
        result = finding(check_part(bracket()), "minimum flange length", "flange")
        assert result.outcome is Outcome.PASSED  # type: ignore[attr-defined]
        assert result.measured == pytest.approx(30.0)  # type: ignore[attr-defined]
        assert result.limit == pytest.approx(13.0)  # type: ignore[attr-defined]

    def test_a_flange_that_cannot_reach_the_die_shoulder_fails(self) -> None:
        report = check_part(bracket(flange_length=12.0))
        result = finding(report, "minimum flange length", "flange")
        assert result.outcome is Outcome.FAILED  # type: ignore[attr-defined]
        assert result.measured == pytest.approx(12.0)  # type: ignore[attr-defined]
        assert "drops into the die" in result.message  # type: ignore[attr-defined]
        assert not report.ok

    def test_the_limit_follows_the_die_the_caller_names(self) -> None:
        """On a 10 mm V the same 12 mm flange is fine: 10/2 + 3 + 2 = 10 mm."""
        report = check_part(bracket(flange_length=12.0), die_opening_mm=10.0)
        result = finding(report, "minimum flange length", "flange")
        assert result.outcome is Outcome.PASSED  # type: ignore[attr-defined]
        assert result.limit == pytest.approx(10.0)  # type: ignore[attr-defined]

    def test_the_failure_names_a_die_that_would_hold_it(self) -> None:
        result = finding(
            check_part(bracket(flange_length=12.0)), "minimum flange length", "flange"
        )
        # V/2 + r + t = 12 gives V = 2*(12 - 2 - 3) = 14 mm.
        assert "14.000 mm opening" in result.message  # type: ignore[attr-defined]

    def test_a_face_with_no_bends_is_not_checked_at_all(self) -> None:
        """A flat plate has no flange to be too short. Silence, not a pass."""
        part = SheetMetalPart(
            name="plate",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=Flange(name="plate", length_mm=100.0, width_mm=50.0),
        )
        report = check_part(part)
        assert [f.check for f in report.findings] == ["K-factor basis"]

    def test_a_hem_is_reported_unmeasured_rather_than_measured_wrongly(self) -> None:
        hem = Bend(
            angle_deg=180.0,
            inside_radius_mm=1.0,
            direction=BendDirection.UP,
            k=K44,
            name="hem",
        )
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(
                Joint(
                    edge=Edge.FAR, bend=hem, flange=Flange(name="return", length_mm=10.0)
                ),
            ),
        )
        part = SheetMetalPart(
            name="hemmed",
            material=steel(),
            convention=LengthConvention.TANGENT,
            root=base,
        )
        report = check_part(part)
        result = finding(report, "minimum flange length", "return")
        assert result.outcome is Outcome.UNMEASURED  # type: ignore[attr-defined]
        assert "no outside mould line" in result.message  # type: ignore[attr-defined]
        assert not report.complete

    def test_a_die_opening_of_zero_is_refused(self) -> None:
        with pytest.raises(FormabilityError, match="not a die"):
            check_part(bracket(), die_opening_mm=0.0)


class TestHoleDistanceToBend:
    def test_a_hole_too_close_to_the_bend_fails(self) -> None:
        # u = 10 from the mould line is 5 from the tangent; a 6 mm hole leaves
        # 5 - 3 = 2 mm of edge distance against a 4 mm limit.
        hole = Hole(name="h1", diameter_mm=6.0, u_mm=10.0, v_mm=50.0)
        report = check_part(bracket(holes=(hole,)))
        result = finding(report, "hole distance to bend")
        assert result.outcome is Outcome.FAILED  # type: ignore[attr-defined]
        assert result.measured == pytest.approx(2.0)  # type: ignore[attr-defined]
        assert result.limit == pytest.approx(4.0)  # type: ignore[attr-defined]
        assert "2.000 mm away" in result.message  # type: ignore[attr-defined]
        assert not report.ok

    def test_moving_it_clear_passes(self) -> None:
        # u = 13 gives 8 from the tangent, less the 3 mm radius: 5 mm.
        hole = Hole(name="h1", diameter_mm=6.0, u_mm=13.0, v_mm=50.0)
        result = finding(check_part(bracket(holes=(hole,))), "hole distance to bend")
        assert result.outcome is Outcome.PASSED  # type: ignore[attr-defined]
        assert result.measured == pytest.approx(5.0)  # type: ignore[attr-defined]

    def test_the_boundary_is_inclusive(self) -> None:
        # 4 mm of edge distance exactly: u = 12 -> 7 from the tangent, less 3.
        hole = Hole(name="h1", diameter_mm=6.0, u_mm=12.0, v_mm=50.0)
        result = finding(check_part(bracket(holes=(hole,))), "hole distance to bend")
        assert result.outcome is Outcome.PASSED  # type: ignore[attr-defined]

    def test_the_factor_can_be_tightened_per_part(self) -> None:
        hole = Hole(name="h1", diameter_mm=6.0, u_mm=13.0, v_mm=50.0)
        report = check_part(bracket(holes=(hole,)), hole_factor=3.0)
        result = finding(report, "hole distance to bend")
        assert result.limit == pytest.approx(6.0)  # type: ignore[attr-defined]
        assert result.outcome is Outcome.FAILED  # type: ignore[attr-defined]

    def test_a_hole_on_a_face_with_no_bend_near_it_is_not_checked_against_one(self) -> None:
        """The root's only bend is on FAR, so only that distance is measured."""
        base_hole = Hole(name="h0", diameter_mm=6.0, u_mm=10.0, v_mm=50.0)
        bend = Bend(
            angle_deg=90.0,
            inside_radius_mm=RADIUS,
            direction=BendDirection.UP,
            k=K44,
            name="b1",
        )
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            holes=(base_hole,),
            joints=(
                Joint(edge=Edge.FAR, bend=bend, flange=Flange(name="flange", length_mm=30.0)),
            ),
        )
        part = SheetMetalPart(
            name="bracket",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        report = check_part(part)
        hole_findings = [f for f in report.findings if f.check == "hole distance to bend"]
        assert len(hole_findings) == 1
        # base tangent length 35, hole centre at 10, radius 3 -> 35 - 10 - 3 = 22.
        assert hole_findings[0].measured == pytest.approx(22.0)


class TestKFactorBasis:
    def test_an_assumed_k_is_unmeasured_and_never_a_failure(self) -> None:
        report = check_part(bracket())
        result = finding(report, "K-factor basis")
        assert result.outcome is Outcome.UNMEASURED  # type: ignore[attr-defined]
        assert "no stated basis" in result.message  # type: ignore[attr-defined]
        assert report.ok
        assert not report.complete

    def test_a_sourced_k_passes(self) -> None:
        part = bracket(k=din6935(inside_radius_mm=RADIUS, thickness_mm=THICKNESS))
        report = check_part(part)
        result = finding(report, "K-factor basis")
        assert result.outcome is Outcome.PASSED  # type: ignore[attr-defined]
        assert report.complete


class TestTheReport:
    def test_a_good_part_is_ok_and_complete(self) -> None:
        part = bracket(k=din6935(inside_radius_mm=RADIUS, thickness_mm=THICKNESS))
        report = check_part(part)
        assert report.ok
        assert report.complete
        assert not report.failed
        assert not report.unmeasured

    def test_every_reason_is_reported_at_once_rather_than_the_first(self) -> None:
        part = bracket(
            inside_radius_mm=1.0,
            flange_length=8.0,
            holes=(Hole(name="h1", diameter_mm=6.0, u_mm=5.0, v_mm=50.0),),
        )
        report = check_part(part)
        failed = {f.check for f in report.failed}
        assert failed == {
            "minimum bend radius",
            "minimum flange length",
            "hole distance to bend",
        }

    def test_raise_for_findings_names_them_all(self) -> None:
        report = check_part(bracket(inside_radius_mm=1.0, flange_length=8.0))
        with pytest.raises(FormabilityError) as caught:
            report.raise_for_findings()
        message = str(caught.value)
        assert "minimum bend radius" in message
        assert "minimum flange length" in message

    def test_raise_for_findings_is_silent_on_a_good_part(self) -> None:
        part = bracket(k=din6935(inside_radius_mm=RADIUS, thickness_mm=THICKNESS))
        check_part(part).raise_for_findings(include_unmeasured=True)

    def test_unmeasured_can_be_made_a_refusal_too(self) -> None:
        report = check_part(bracket())  # assumed K
        report.raise_for_findings()  # nothing failed
        with pytest.raises(FormabilityError, match="K-factor basis"):
            report.raise_for_findings(include_unmeasured=True)

    def test_the_summary_says_both_questions(self) -> None:
        text = check_part(bracket()).summary()
        assert "unmeasured" in text
        assert "not a pass" in text

    def test_to_dict_carries_the_sources(self) -> None:
        payload = check_part(bracket()).to_dict()
        assert payload["ok"] is True
        assert payload["complete"] is False
        for entry in payload["findings"]:
            assert entry["source"]["citation"]

    def test_a_part_that_cannot_be_flattened_raises_rather_than_being_assessed(self) -> None:
        """Geometry first: there is no point measuring a part with no blank."""
        from app.sheetmetal.errors import UnfoldError

        part = bracket(flange_length=3.0)  # shorter than its own 5 mm setback
        with pytest.raises(UnfoldError):
            check_part(part)
