"""A cycle split into channels reassembles to each instant's load exactly (E9.4).

The claim of `app.dynamics.channels` is one identity: at every sampled instant, the sum of
channel load times channel signal is the load `loadcases.build` would put on the part at
that instant. So that is what is tested, at every instant, to rounding:

* a whirling mass on a plain force mount, whose reaction turns all the way round
  (`m ω² r`, three axis channels, one of them dropped);
* a harmonic slider on a bearing mount, whose reaction reverses along one line (two
  one-sided channels, never both loaded at once);
* the body's own `g − a(t)` field;
* and the bearing mount on the whirling mass, which must be refused, because a turning
  bearing load is not a scaled copy of one solve.

Then the last link: the channels become `LoadChannel`s and a node's history is the
superposition. No solver is needed for that, because superposition is the property
being tested and a synthetic tensor per channel makes it exact.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from app.dynamics import channels, kinematics, loadcases, reactions
from app.dynamics.errors import MechanismError
from app.dynamics.types import Body, Driver, Joint, Mechanism, MechanismResult, MotionRange
from app.solve.types import BoxSelector, Fixture, GravityLoad, Material

STEEL = Material(
    name="test steel",
    youngs_modulus_mpa=200_000.0,
    poissons_ratio=0.3,
    yield_strength_mpa=250.0,
    density_kg_m3=7850.0,
)
FIXTURES = [Fixture(where=BoxSelector(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0)))]
ZERO_G = (0.0, 0.0, 0.0)


def _result(mechanism: Mechanism, samples: int = 13) -> MechanismResult:
    path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=samples))
    return MechanismResult(
        mechanism=mechanism.name,
        engine="closed-form serial-chain recursion",
        path=path,
        reactions=reactions.compute(mechanism, path),
    )


def _whirl() -> Mechanism:
    return Mechanism(
        name="whirl",
        bodies=(Body(name="rotor", mass_kg=2.0, centre_of_mass_mm=(30.0, 0.0, 0.0)),),
        joints=(Joint(name="shaft", kind="revolute", body="rotor", axis=(0.0, 0.0, 1.0)),),
        drivers=(Driver(joint="shaft", kind="constant", rate=7.0),),
        gravity_mm_s2=ZERO_G,
    )


def _slider() -> Mechanism:
    return Mechanism(
        name="slider",
        bodies=(Body(name="ram", mass_kg=5.0),),
        joints=(Joint(name="guide", kind="prismatic", body="ram", axis=(1.0, 0.0, 0.0)),),
        drivers=(Driver(joint="guide", kind="harmonic", amplitude=40.0, frequency_hz=2.0),),
        gravity_mm_s2=ZERO_G,
    )


def _mount(joint: str, *, bearing: bool) -> loadcases.JointMount:
    return loadcases.cylinder_mount(
        joint,
        axis_point=(0.0, 0.0, 0.0),
        axis_direction=(0.0, 0.0, 1.0),
        radius_mm=10.0,
        bearing=bearing,
    )


class TestAPlainForceMountSplitsByAxis:
    def test_every_instant_reassembles_to_the_reaction(self) -> None:
        result = _result(_whirl())
        cycle = channels.plan(
            result,
            mounts=[_mount("shaft", bearing=False)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )
        forces = result.reaction("shaft").force_n
        for i, expected in enumerate(forces):
            [(label, total)] = channels.instant(cycle, i)["forces_n"].items()
            assert label == "shaft reaction"
            assert total == pytest.approx(expected, abs=1e-12)

    def test_the_axis_that_carries_nothing_is_dropped_and_named(self) -> None:
        cycle = channels.plan(
            _result(_whirl()),
            mounts=[_mount("shaft", bearing=False)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )
        assert [c.name for c in cycle.channels] == ["shaft reaction x", "shaft reaction y"]
        assert cycle.dropped == ("shaft reaction z",)

    def test_each_channel_is_solved_at_its_peak_so_the_signal_stays_in_unit_range(self) -> None:
        cycle = channels.plan(
            _result(_whirl()),
            mounts=[_mount("shaft", bearing=False)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )
        peak = 2.0 * 7.0**2 * 30.0 * 1e-3
        for channel in cycle.channels:
            assert max(abs(s) for s in channel.signal) == pytest.approx(1.0)
        # At t = 0 the centre is on +x, so the x channel is solved at the full m ω² r;
        # y is solved at its largest *sampled* value, which cannot exceed it.
        x, y = (c.case.loads[0] for c in cycle.channels)
        assert abs(x.force_n[0]) == pytest.approx(peak, rel=1e-12)
        assert 0.0 < abs(y.force_n[1]) <= peak * (1.0 + 1e-12)

    def test_a_turning_reaction_on_a_bearing_mount_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="turns by") as caught:
            channels.plan(
                _result(_whirl()),
                mounts=[_mount("shaft", bearing=True)],
                material=STEEL,
                fixtures=FIXTURES,
                gravity_mm_s2=ZERO_G,
            )
        assert "bearing=False" in str(caught.value)


class TestABearingMountSplitsAlongItsLine:
    def test_a_reversing_reaction_becomes_two_one_sided_channels(self) -> None:
        cycle = channels.plan(
            _result(_slider()),
            mounts=[_mount("guide", bearing=True)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )
        names = [c.name for c in cycle.channels]
        assert names == ["guide reaction +", "guide reaction -"]
        push, pull = cycle.channels
        for a, b in zip(push.signal, pull.signal, strict=True):
            assert a >= 0.0 and b >= 0.0
            assert a == 0.0 or b == 0.0

    def test_every_instant_reassembles_to_the_reaction(self) -> None:
        result = _result(_slider())
        cycle = channels.plan(
            result,
            mounts=[_mount("guide", bearing=True)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )
        for i, expected in enumerate(result.reaction("guide").force_n):
            total = channels.instant(cycle, i)["forces_n"]["guide reaction"]
            assert total == pytest.approx(expected, abs=1e-9)

    def test_the_peak_is_m_a_at_the_end_of_the_stroke(self) -> None:
        cycle = channels.plan(
            _result(_slider(), samples=17),
            mounts=[_mount("guide", bearing=True)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )
        omega = 2.0 * math.pi * 2.0
        peak = 5.0 * 40.0 * omega * omega * 1e-3
        [load] = cycle.channels[0].case.loads
        assert math.hypot(*load.force_n) == pytest.approx(peak, rel=1e-9)


class TestTheBodysOwnInertia:
    def test_the_field_reassembles_to_g_minus_a(self) -> None:
        mechanism = _whirl()
        gravity = (0.0, 0.0, -9806.65)
        result = _result(mechanism)
        cycle = channels.plan(
            result,
            mounts=[],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=gravity,
            inertia_of="rotor",
        )
        accelerations = result.path.motion("rotor").acceleration_mm_s2
        for i, a in enumerate(accelerations):
            expected = tuple(g - ak for g, ak in zip(gravity, a, strict=True))
            assert channels.instant(cycle, i)["field_mm_s2"] == pytest.approx(expected, abs=1e-6)
        assert all(isinstance(c.case.loads[0], GravityLoad) for c in cycle.channels)
        assert any("omega^2*r" in note for note in cycle.notes)


class TestWhatThePlanRefuses:
    def test_fewer_than_three_samples(self) -> None:
        with pytest.raises(MechanismError, match="at least 3"):
            channels.plan(
                _result(_whirl(), samples=2),
                mounts=[_mount("shaft", bearing=False)],
                material=STEEL,
                fixtures=FIXTURES,
                gravity_mm_s2=ZERO_G,
            )

    def test_no_fixture(self) -> None:
        with pytest.raises(MechanismError, match="fixture"):
            channels.plan(
                _result(_whirl()),
                mounts=[_mount("shaft", bearing=False)],
                material=STEEL,
                fixtures=[],
                gravity_mm_s2=ZERO_G,
            )

    def test_nothing_to_load(self) -> None:
        with pytest.raises(MechanismError, match="Nothing loads"):
            channels.plan(
                _result(_whirl()), mounts=[], material=STEEL, fixtures=FIXTURES, gravity_mm_s2=ZERO_G
            )

    def test_every_note_says_the_history_is_sampled(self) -> None:
        cycle = channels.plan(
            _result(_whirl()),
            mounts=[_mount("shaft", bearing=False)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )
        assert "lower bound" in cycle.notes[0]


class TestTheChannelsReachFatigue:
    def _cycle(self) -> channels.CyclePlan:
        return channels.plan(
            _result(_whirl()),
            mounts=[_mount("shaft", bearing=False)],
            material=STEEL,
            fixtures=FIXTURES,
            gravity_mm_s2=ZERO_G,
        )

    def test_a_channel_with_no_solve_is_refused(self) -> None:
        cycle = self._cycle()
        with pytest.raises(MechanismError, match="shaft reaction y"):
            cycle.load_channels({"shaft reaction x": SimpleNamespace(nodal_stress=np.zeros((1, 6)))})

    def test_a_nodes_history_is_the_superposition_of_the_channels(self) -> None:
        from app.fatigue.field import Direction, Scalar, history_at

        cycle = self._cycle()
        # Channel x makes σxx = 10 MPa at its peak load, channel y makes σxx = 4 MPa.
        per_channel = {"shaft reaction x": 10.0, "shaft reaction y": 4.0}
        outputs = {}
        for name, value in per_channel.items():
            tensor = np.zeros((1, 6))
            tensor[0, 0] = value
            outputs[name] = SimpleNamespace(nodal_stress=tensor)

        loaded = cycle.load_channels(outputs, solver="linear-static")
        read = history_at(
            loaded,
            0,
            scalar=Scalar.COMPONENT,
            direction=Direction(vector=(1.0, 0.0, 0.0), reason="the lug's axis"),
        )

        x, y = cycle.channels
        expected = [10.0 * sx + 4.0 * sy for sx, sy in zip(x.signal, y.signal, strict=True)]
        assert list(read.history.values_mpa) == pytest.approx(expected, abs=1e-9)
        assert "whirl" in read.history.source


class TestTheSuperpositionHoldsThroughARealSolve:
    """The same whirling load on a clamped 10 × 10 × 40 bar: two channel solves, scaled
    and summed, against one solve of `loadcases.build` at each of three instants. A
    linear solve makes them equal to rounding; the test is that nothing in the split
    (a sign, a normalisation, a swapped axis) breaks it."""

    def test_the_scaled_channels_equal_the_direct_solve_at_every_instant(self) -> None:
        from app.mesh.primitives import box_mesh, promote_to_tet10
        from app.solve.linear_static import LinearStaticSolver
        from app.solve.types import FaceSelector

        mesh = promote_to_tet10(box_mesh((10.0, 10.0, 40.0), divisions=(2, 2, 4)))
        clamp = [Fixture(where=FaceSelector(axis="z", side="min"))]
        mount = loadcases.JointMount(
            joint="shaft", where=FaceSelector(axis="z", side="max"), bearing=False
        )
        result = _result(_whirl(), samples=7)
        cycle = channels.plan(
            result, mounts=[mount], material=STEEL, fixtures=clamp, gravity_mm_s2=ZERO_G
        )
        solver = LinearStaticSolver()
        solved = {c.name: solver.solve(mesh, c.case).nodal_stress for c in cycle.channels}

        for index in (1, 3, 5):
            direct = solver.solve(
                mesh,
                loadcases.build(
                    result,
                    mounts=[mount],
                    material=STEEL,
                    fixtures=clamp,
                    gravity_mm_s2=ZERO_G,
                    at=index,
                ).case,
            ).nodal_stress
            summed = sum(
                c.signal[index] * np.asarray(solved[c.name]) for c in cycle.channels
            )
            scale = float(np.abs(direct).max())
            assert np.allclose(summed, direct, rtol=0.0, atol=1e-8 * scale)
