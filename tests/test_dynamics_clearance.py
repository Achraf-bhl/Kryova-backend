"""Clearance through a motion range — master plan Phase 9, and gate G3's first need.

This is what turns "two parts that fit" into "a mechanism that works", and the
reason it is the first thing worth building in E9 is that it needs no dynamics
engine at all: poses in, distances out.

Two properties matter, and they pull in opposite directions.

**The middle of the travel is where a mechanism fouls.** A check at the two end
poses is the natural thing to write and is exactly the check that misses. A
swinging arm clears at full extension and full retraction and hits the frame at
forty degrees; a cam clears at both dwells. So the sweep has to look *between*
the ends, and a test that only pins the endpoints would pass on a check that
never did.

**An unlooked-at pose is never counted as a measured one.** `stop_on_collision`
short-circuits at the first overlap, which is right for the cheap yes/no
question over a long sweep — but it shipped with `measured_poses` computed as
`samples - len(failures)`, so a sweep that stopped at pose 9 of 21 published
`measured_pose_count: 21`. Twelve poses nobody looked at, counted as measured,
in the payload an assertion reads. `attempted` and `stopped_early` exist because
of that, and the provenance text says the interference volume is the *first*
found rather than the largest.

The measurer is duck-typed — anything with `distance_mm`, `interference_mm3` and
`failure` — so these tests need no geometry kernel and run in milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.dynamics.clearance import sweep
from app.dynamics.pose import Frame
from app.dynamics.types import BodyMotion, MotionPath

SAMPLES = 21


@dataclass
class Reading:
    """What a pose measurer hands back. Only three attributes are read."""

    distance_mm: float | None = None
    interference_mm3: float = 0.0
    failure: str = ""


def _path(samples: int = SAMPLES) -> MotionPath:
    """Two bodies whose poses are irrelevant — the measurer decides the answer.

    The sweep's job here is orchestration: which poses get looked at, what is
    counted, and what the record says. Driving that from a stub measurer keeps
    the test about the sweep rather than about geometry.
    """
    times = tuple(float(i) for i in range(samples))
    zero = tuple((0.0, 0.0, 0.0) for _ in range(samples))
    frames = tuple(Frame() for _ in range(samples))

    def motion(name: str) -> BodyMotion:
        return BodyMotion(
            body=name,
            times_s=times,
            frames=frames,
            position_mm=zero,
            velocity_mm_s=zero,
            angular_velocity_rad_s=zero,
            acceleration_mm_s2=zero,
            angular_acceleration_rad_s2=zero,
        )

    return MotionPath(
        mechanism="test",
        times_s=times,
        bodies={"arm": motion("arm"), "frame": motion("frame")},
    )


class TestTheMiddleOfTheTravel:
    def test_a_collision_only_in_the_middle_is_caught(self) -> None:
        """The case a two-pose check misses, and the reason a sweep exists."""
        seen: list[int] = []

        def measure(a, frame_a, b, frame_b):
            index = len(seen)
            seen.append(index)
            if index == SAMPLES // 2:
                return Reading(distance_mm=-2.0, interference_mm3=500.0)
            return Reading(distance_mm=25.0)

        result = sweep(_path(), "arm", "frame", measure)

        assert result.collides
        assert result.interference_mm3 == pytest.approx(500.0)

    def test_the_end_poses_alone_would_have_said_it_was_fine(self) -> None:
        """Stated explicitly so the test above cannot be weakened into an
        endpoint check without this one failing."""
        readings: list[Reading] = []

        def measure(a, frame_a, b, frame_b):
            index = len(readings)
            reading = (
                Reading(distance_mm=-2.0, interference_mm3=500.0)
                if index == SAMPLES // 2
                else Reading(distance_mm=25.0)
            )
            readings.append(reading)
            return reading

        sweep(_path(), "arm", "frame", measure)

        assert readings[0].distance_mm == 25.0
        assert readings[-1].distance_mm == 25.0
        assert any(r.interference_mm3 > 0 for r in readings)

    def test_every_pose_is_visited_when_nothing_collides(self) -> None:
        count = 0

        def measure(a, frame_a, b, frame_b):
            nonlocal count
            count += 1
            return Reading(distance_mm=10.0)

        result = sweep(_path(), "arm", "frame", measure)

        assert count == SAMPLES
        assert result.attempted == SAMPLES
        assert result.stopped_early is False

    def test_the_minimum_is_the_smallest_over_the_sweep(self) -> None:
        distances = [10.0] * SAMPLES
        distances[7] = 1.5

        def counting(a, frame_a, b, frame_b):
            value = distances[counting.calls]
            counting.calls += 1
            return Reading(distance_mm=value)

        counting.calls = 0

        result = sweep(_path(), "arm", "frame", counting)

        assert result.minimum_mm == pytest.approx(1.5)


class TestAnUnlookedAtPoseIsNotAMeasuredOne:
    """The defect: a sweep that stopped at pose 9 of 21 published
    `measured_pose_count: 21`."""

    def _stopping(self):
        state = {"calls": 0}

        def measure(a, frame_a, b, frame_b):
            index = state["calls"]
            state["calls"] += 1
            if index == 8:
                return Reading(distance_mm=-1.0, interference_mm3=100.0)
            return Reading(distance_mm=5.0)

        return measure, state

    def test_it_stops_and_says_that_it_stopped(self) -> None:
        measure, state = self._stopping()

        result = sweep(_path(), "arm", "frame", measure, stop_on_collision=True)

        assert result.stopped_early is True
        assert state["calls"] < SAMPLES

    def test_attempted_counts_only_the_poses_actually_asked_about(self) -> None:
        measure, state = self._stopping()

        result = sweep(_path(), "arm", "frame", measure, stop_on_collision=True)

        assert result.attempted == state["calls"]
        assert result.attempted < SAMPLES

    def test_the_payload_does_not_claim_the_unvisited_poses(self) -> None:
        """The half that reaches an assertion, and therefore the half that
        mattered: a payload saying 21 poses were measured when 9 were is a
        false statement in a true-looking number."""
        measure, _ = self._stopping()

        result = sweep(_path(), "arm", "frame", measure, stop_on_collision=True)
        payload = result.to_payload()

        assert payload["stopped_early"] is True
        assert result.measured_poses <= result.attempted

    def test_the_provenance_says_the_overlap_is_the_first_not_the_worst(self) -> None:
        """A caller steering a correction loop needs the *worst* overlap to know
        how far to move something. The first one only says something is wrong,
        and the record must not let the two be confused."""
        measure, _ = self._stopping()

        result = sweep(_path(), "arm", "frame", measure, stop_on_collision=True)
        payload = result.to_payload()

        text = str(payload.get("provenance", payload))
        assert "not the worst" in text

    def test_a_complete_sweep_does_not_carry_that_caveat(self) -> None:
        """The other side: a sweep that finished must not be hedged, or the
        hedge stops meaning anything."""

        def measure(a, frame_a, b, frame_b):
            return Reading(distance_mm=5.0)

        result = sweep(_path(), "arm", "frame", measure, stop_on_collision=True)
        payload = result.to_payload()

        assert result.stopped_early is False
        assert "not the worst" not in str(payload.get("provenance", payload))


class TestAMeasurerIsAllowedToFail:
    def test_one_bad_pose_does_not_lose_the_others(self) -> None:
        """The answer over the other twenty poses is still worth having, and the
        failure count is what says whether to believe it."""

        def measure(a, frame_a, b, frame_b):
            measure.calls = getattr(measure, "calls", 0) + 1
            if measure.calls == 5:
                raise RuntimeError("the kernel could not place this pose")
            return Reading(distance_mm=7.0)

        result = sweep(_path(), "arm", "frame", measure)

        assert result.failures
        assert result.measured_poses == SAMPLES - len(result.failures)
        assert result.minimum_mm == pytest.approx(7.0)

    def test_a_pose_with_no_distance_is_a_failure_not_a_zero(self) -> None:
        """A missing distance read as 0.0 would report a clash at every pose the
        measurer could not answer — the worst possible direction to be wrong in,
        because it looks like the check working."""

        def measure(a, frame_a, b, frame_b):
            measure.calls = getattr(measure, "calls", 0) + 1
            if measure.calls == 3:
                return Reading(distance_mm=None, failure="no common normal")
            return Reading(distance_mm=4.0)

        result = sweep(_path(), "arm", "frame", measure)

        assert result.minimum_mm == pytest.approx(4.0)
        assert len(result.failures) == 1
