"""A CMM measurement plan from the GD&T scheme: order, points and what is refused (E17.5).

Point counts are test fixtures with a source that says so. Every laid point is checked to lie
on its nominal geometry, which is the property a plan must not get wrong.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import math

import pytest

from app.manufacture.inspection import (
    CylinderFeature,
    InspectionError,
    PlaneFeature,
    StepKind,
    plan_inspection,
)
from app.rules.gdt import (
    Characteristic,
    Datum,
    DatumReference,
    DatumScheme,
    FeatureControlFrame,
    MaterialCondition,
    Tolerancing,
)
from app.rules.processes import Limit

FIXTURE = "test fixture sampling, not a company standard"

BASE = PlaneFeature("base", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0), 80.0, 40.0)
SIDE = PlaneFeature("side", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), 20.0, 80.0)
BORE = CylinderFeature("bore", (40.0, 20.0, 0.0), (0.0, 0.0, 1.0), 6.0, 20.0, internal=True)
BOSS = CylinderFeature("boss", (10.0, 10.0, 20.0), (0.0, 0.0, 1.0), 5.0, 10.0, internal=False)
FEATURES = {f.name: f for f in (BASE, SIDE, BORE, BOSS)}


def _tolerancing(*frames: FeatureControlFrame) -> Tolerancing:
    return Tolerancing(
        scheme=DatumScheme((Datum("A", "base"), Datum("B", "side"))),
        frames=frames,
    )


POSITION_AT_MMC = FeatureControlFrame(
    "bore",
    Characteristic.POSITION,
    0.2,
    datums=(DatumReference("A"), DatumReference("B")),
    condition=MaterialCondition.MMC,
    diametral=True,
)
FLATNESS = FeatureControlFrame("base", Characteristic.FLATNESS, 0.05)
SAMPLING = {"plane": Limit(9, FIXTURE), "cylinder": Limit(12, FIXTURE)}


class TestTheOrder:
    def test_datums_first_in_precedence_then_size_then_the_tolerance(self) -> None:
        result = plan_inspection(_tolerancing(POSITION_AT_MMC), FEATURES, SAMPLING)
        assert [(s.kind, s.feature) for s in result.steps] == [
            (StepKind.ALIGN, "base"),
            (StepKind.ALIGN, "side"),
            (StepKind.SIZE, "bore"),
            (StepKind.TOLERANCE, "bore"),
        ]
        assert result.steps[-1].datums == ("A", "B")
        assert result.complete

    def test_a_tolerance_regardless_of_size_needs_no_size_step(self) -> None:
        result = plan_inspection(_tolerancing(FLATNESS), FEATURES, SAMPLING)
        assert StepKind.SIZE not in [s.kind for s in result.steps]

    def test_each_step_names_what_the_cmm_evaluates(self) -> None:
        step = plan_inspection(_tolerancing(FLATNESS), FEATURES, SAMPLING).steps[-1]
        assert "fit a plane" in step.what
        assert step.sampling_source == FIXTURE


class TestThePointsAreOnTheFeature:
    def test_a_plane_gets_the_count_asked_for_inside_its_rectangle(self) -> None:
        points = BASE.points(9)
        assert len(points) == 9
        for p in points:
            x, y, z = p.at_mm
            assert z == pytest.approx(0.0)
            assert 0.0 < x < 80.0 and -40.0 < y < 0.0
            # outward normal u x v = x cross -y = -z, so the probe travels +z.
            assert p.approach == pytest.approx((0.0, 0.0, 1.0))

    @pytest.mark.parametrize("feature", [BORE, BOSS], ids=["bore", "boss"])
    def test_a_cylinder_gets_rings_on_its_radius(self, feature: CylinderFeature) -> None:
        points = feature.points(12)
        assert len(points) == 12
        for p in points:
            dx = p.at_mm[0] - feature.base_mm[0]
            dy = p.at_mm[1] - feature.base_mm[1]
            assert math.hypot(dx, dy) == pytest.approx(feature.radius_mm)
            height = p.at_mm[2] - feature.base_mm[2]
            assert 0.0 < height < feature.length_mm
            radial = (dx / feature.radius_mm, dy / feature.radius_mm)
            inward = -1.0 if not feature.internal else 1.0
            assert p.approach[0] == pytest.approx(inward * radial[0])
            assert p.approach[1] == pytest.approx(inward * radial[1])

    def test_a_cylinder_is_spread_over_at_least_two_rings(self) -> None:
        heights = {round(p.at_mm[2], 9) for p in BORE.points(6)}
        assert len(heights) >= 2


class TestWhatIsNotComplete:
    def test_a_feature_with_no_geometry_is_unresolved(self) -> None:
        frame = FeatureControlFrame("slot", Characteristic.FLATNESS, 0.05)
        result = plan_inspection(_tolerancing(frame), FEATURES, SAMPLING)
        assert result.unresolved == ("slot",)
        assert not result.complete

    def test_a_feature_with_no_sampling_is_unset(self) -> None:
        result = plan_inspection(_tolerancing(POSITION_AT_MMC), FEATURES, {"plane": Limit(9, FIXTURE)})
        assert result.unset == ("bore",)
        assert not result.complete

    def test_a_name_overrides_the_geometry_default(self) -> None:
        sampling = {**SAMPLING, "base": Limit(16, FIXTURE)}
        step = plan_inspection(_tolerancing(FLATNESS), FEATURES, sampling).steps[-1]
        assert len(step.points) == 16

    def test_no_datum_scheme_is_said(self) -> None:
        tolerancing = Tolerancing(frames=(FLATNESS,))
        result = plan_inspection(tolerancing, FEATURES, SAMPLING)
        assert any("only form" in note for note in result.notes)


class TestWhatIsRefused:
    def test_a_form_tolerance_at_the_determining_count_measures_nothing(self) -> None:
        with pytest.raises(InspectionError, match="zero on any part"):
            plan_inspection(_tolerancing(FLATNESS), FEATURES, {**SAMPLING, "base": Limit(3, FIXTURE)})

    def test_fewer_points_than_determine_the_geometry(self) -> None:
        with pytest.raises(InspectionError, match="needs 5"):
            plan_inspection(_tolerancing(POSITION_AT_MMC), FEATURES, {**SAMPLING, "cylinder": Limit(4, FIXTURE)})

    def test_a_fractional_count(self) -> None:
        with pytest.raises(InspectionError, match="whole number"):
            plan_inspection(_tolerancing(FLATNESS), FEATURES, {**SAMPLING, "plane": Limit(8.5, FIXTURE)})

    def test_a_feature_filed_under_another_name(self) -> None:
        with pytest.raises(InspectionError, match="called"):
            plan_inspection(_tolerancing(FLATNESS), {"base": SIDE}, SAMPLING)

    def test_u_and_v_not_square(self) -> None:
        with pytest.raises(InspectionError, match="perpendicular"):
            PlaneFeature("skew", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), 1.0, 1.0)
