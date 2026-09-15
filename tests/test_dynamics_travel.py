"""Travel, lock and swept space, each against an answer known before the code runs (E9.3).

* **Travel** is exact, so a harmonic driver sampled at only its two endpoints must still
  report `offset ± amplitude`: the peak between the samples is found by solving for the
  phase, not by looking.
* **Transmission angle** has a closed form at a crank-rocker's two in-line positions, by
  the law of cosines on the triangle of coupler, output and the pivot span:
  `cos μ = (b² + c² − (g ∓ a)²) / 2bc`.
* **A non-Grashof linkage locks**, and the sweep says where.
* **Swept envelope** of a point on a whirling arm is the circle's bounding square.
* **Swept volume** of a 10 mm cube at two poses 5 mm apart is the 15 × 10 × 10 union, on
  the real kernel.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import math

import pytest

from app.design.assertions import Outcome
from app.dynamics import kinematics
from app.dynamics.errors import MechanismError
from app.dynamics.pose import Frame
from app.dynamics.travel import (
    TravelLimit,
    coordinate_extremes,
    lock_sweep,
    occt_union_volume,
    swept_envelope,
    swept_volume,
    transmission_angle_deg,
    travel,
)
from app.dynamics.types import Body, Driver, Joint, Mechanism, MotionRange

SOURCE = "test fixture: stops read off a drawing nobody made"


class TestExtremesAreExactNotSampled:
    def test_a_constant_rate_peaks_at_the_ends(self) -> None:
        low, t_low, high, t_high = coordinate_extremes(
            Driver(joint="j", kind="constant", offset=1.0, rate=2.0), 3.0
        )
        assert (low, t_low, high, t_high) == pytest.approx((1.0, 0.0, 7.0, 3.0))

    def test_a_harmonic_over_a_whole_period_reaches_both_amplitudes(self) -> None:
        driver = Driver(joint="j", kind="harmonic", offset=5.0, amplitude=2.0, frequency_hz=1.0)
        low, t_low, high, t_high = coordinate_extremes(driver, 1.0)
        assert high == pytest.approx(7.0, abs=1e-12)
        assert t_high == pytest.approx(0.25, abs=1e-12)
        assert low == pytest.approx(3.0, abs=1e-12)
        assert t_low == pytest.approx(0.75, abs=1e-12)

    def test_a_phase_shift_moves_the_peak_instant(self) -> None:
        driver = Driver(
            joint="j", kind="harmonic", amplitude=1.0, frequency_hz=2.0, phase_rad=math.pi / 4.0
        )
        _, _, high, t_high = coordinate_extremes(driver, 0.5)
        assert high == pytest.approx(1.0, abs=1e-12)
        # 2π·2·t + π/4 = π/2  →  t = 1/32 s.
        assert t_high == pytest.approx(1.0 / 32.0, abs=1e-12)

    def test_a_harmonic_that_never_reaches_its_crest_peaks_at_the_end(self) -> None:
        driver = Driver(joint="j", kind="harmonic", amplitude=1.0, frequency_hz=1.0)
        _, _, high, t_high = coordinate_extremes(driver, 0.1)
        assert high == pytest.approx(math.sin(2.0 * math.pi * 0.1), abs=1e-12)
        assert t_high == pytest.approx(0.1)

    def test_a_table_peaks_at_a_knot(self) -> None:
        driver = Driver(
            joint="j", kind="table", times_s=(0.0, 1.0, 2.0, 3.0), values=(0.0, 4.0, -1.0, 2.0)
        )
        low, t_low, high, t_high = coordinate_extremes(driver, 3.0)
        assert (low, t_low, high, t_high) == pytest.approx((-1.0, 2.0, 4.0, 1.0))


class TestTravelAgainstTheStops:
    def test_a_peak_between_the_only_two_samples_is_still_caught(self) -> None:
        driver = Driver(joint="slide", kind="harmonic", amplitude=50.0, frequency_hz=1.0)
        limit = TravelLimit(joint="slide", lower=-60.0, upper=40.0, source=SOURCE)

        report = travel(driver, limit, MotionRange(duration_s=1.0, samples=2))

        assert report.outcome is Outcome.FAILED
        assert report.maximum == pytest.approx(50.0)
        assert report.margin == pytest.approx(-10.0)
        assert "upper stop" in report.summary
        assert SOURCE in report.summary

    def test_inside_the_stops_passes_with_its_margin(self) -> None:
        driver = Driver(joint="slide", kind="harmonic", amplitude=30.0, frequency_hz=1.0)
        limit = TravelLimit(joint="slide", lower=-40.0, upper=45.0, source=SOURCE)
        report = travel(driver, limit, MotionRange(duration_s=2.0))
        assert report.outcome is Outcome.PASSED
        assert report.margin == pytest.approx(10.0)

    def test_a_limit_with_no_source_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="no source"):
            TravelLimit(joint="slide", lower=0.0, upper=1.0, source=" ")

    def test_an_inverted_limit_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="lower stop below"):
            TravelLimit(joint="slide", lower=1.0, upper=0.0, source=SOURCE)

    def test_a_limit_on_another_joint_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="Pair each limit"):
            travel(
                Driver(joint="a"),
                TravelLimit(joint="b", lower=0.0, upper=1.0, source=SOURCE),
                MotionRange(duration_s=1.0),
            )


def _transmission_at_in_line(g: float, a: float, b: float, c: float, outer: bool) -> float:
    span = g + a if outer else g - a
    mu = math.degrees(math.acos((b * b + c * c - span * span) / (2.0 * b * c)))
    return min(mu, 180.0 - mu)


class TestTheLinkageLocksOrBinds:
    G, A, B, C = 100.0, 30.0, 90.0, 80.0  # a Grashof crank-rocker: 30 + 100 < 90 + 80

    def test_the_acute_fold_treats_thirty_and_one_fifty_alike(self) -> None:
        assert transmission_angle_deg(0.0, math.radians(30.0)) == pytest.approx(30.0)
        assert transmission_angle_deg(0.0, math.radians(150.0)) == pytest.approx(30.0)

    def test_a_crank_rocker_turns_round_and_its_worst_angle_is_the_closed_form(self) -> None:
        expected = min(
            _transmission_at_in_line(self.G, self.A, self.B, self.C, outer=False),
            _transmission_at_in_line(self.G, self.A, self.B, self.C, outer=True),
        )
        assert expected == pytest.approx(48.19, abs=0.01)

        report = lock_sweep(
            ground_mm=self.G,
            input_mm=self.A,
            coupler_mm=self.B,
            output_mm=self.C,
            start_deg=0.0,
            end_deg=360.0,
            samples=361,
            minimum_transmission_deg=40.0,
            source=SOURCE,
        )

        assert report.locked_at_deg is None
        assert report.minimum_transmission_deg == pytest.approx(expected, abs=1e-9)
        assert report.at_input_deg in (pytest.approx(0.0), pytest.approx(360.0))
        assert report.outcome is Outcome.PASSED
        assert "upper bound" in report.summary

    def test_a_stricter_criterion_fails_the_same_linkage(self) -> None:
        report = lock_sweep(
            ground_mm=self.G,
            input_mm=self.A,
            coupler_mm=self.B,
            output_mm=self.C,
            start_deg=0.0,
            end_deg=360.0,
            samples=73,
            minimum_transmission_deg=50.0,
            source=SOURCE,
        )
        assert report.outcome is Outcome.FAILED
        assert "below the 50" in report.summary

    def test_a_non_grashof_linkage_locks_and_the_sweep_says_where(self) -> None:
        report = lock_sweep(
            ground_mm=100.0,
            input_mm=60.0,
            coupler_mm=30.0,
            output_mm=40.0,
            start_deg=0.0,
            end_deg=180.0,
            samples=181,
            minimum_transmission_deg=30.0,
            source=SOURCE,
        )
        assert report.outcome is Outcome.FAILED
        assert report.locked_at_deg is not None
        assert 0.0 < report.locked_at_deg <= 180.0
        assert "cannot be assembled" in report.lock_reason

    def test_a_criterion_with_no_source_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="no source"):
            lock_sweep(
                ground_mm=self.G,
                input_mm=self.A,
                coupler_mm=self.B,
                output_mm=self.C,
                start_deg=0.0,
                end_deg=10.0,
                samples=3,
                minimum_transmission_deg=40.0,
                source="",
            )


def _arm(rate: float = 2.0 * math.pi) -> Mechanism:
    return Mechanism(
        name="arm",
        bodies=(Body(name="arm", mass_kg=1.0, centre_of_mass_mm=(25.0, 0.0, 0.0)),),
        joints=(Joint(name="pivot", kind="revolute", body="arm", axis=(0.0, 0.0, 1.0)),),
        drivers=(Driver(joint="pivot", kind="constant", rate=rate),),
        gravity_mm_s2=(0.0, 0.0, 0.0),
    )


class TestTheSpaceItSweeps:
    def test_a_point_on_a_whirling_arm_boxes_the_circle(self) -> None:
        path = kinematics.evaluate(_arm(), MotionRange(duration_s=1.0, samples=5))
        envelope = swept_envelope(path, "arm", [(50.0, 0.0, 0.0)])
        assert envelope.minimum_mm == pytest.approx((-50.0, -50.0, 0.0), abs=1e-9)
        assert envelope.maximum_mm == pytest.approx((50.0, 50.0, 0.0), abs=1e-9)
        assert "sampled" in envelope.method

    def test_the_union_measurer_sees_every_pose(self) -> None:
        path = kinematics.evaluate(_arm(), MotionRange(duration_s=1.0, samples=7))
        seen: list[int] = []

        def union(frames: list[Frame]) -> float:
            seen.append(len(frames))
            return 123.0

        result = swept_volume(path, "arm", union)  # type: ignore[arg-type]
        assert seen == [7]
        assert result.lower_bound_mm3 == 123.0
        assert "lower bound" in result.method

    def test_a_negative_volume_is_refused(self) -> None:
        path = kinematics.evaluate(_arm(), MotionRange(duration_s=1.0, samples=3))
        with pytest.raises(MechanismError, match="not negative"):
            swept_volume(path, "arm", lambda frames: -1.0)

    def test_two_overlapping_cubes_fuse_to_the_union_on_the_real_kernel(self) -> None:
        from app.kernel.occt.binding import available, symbol

        if not available():
            pytest.skip("OCCT is not installed")
        cube = symbol("BRepPrimAPI_MakeBox")(10.0, 10.0, 10.0).Shape()
        measure = occt_union_volume(cube)
        volume = measure([Frame(), Frame(origin_mm=(5.0, 0.0, 0.0))])
        assert volume == pytest.approx(1500.0, rel=1e-6)
