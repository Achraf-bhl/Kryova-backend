"""Project Chrono across a container boundary — master plan E9.1.

**Written on Linux on 2026-09-16 and not run as pytest**: the user's rule for this stretch
is that this machine writes code and tests and the Windows machine runs them. Every
assertion here was measured first by one-off scripts driving the real modules, and — once
the image existed — the real engine.

**What these tests prove, and what the oracle runs proved instead.** These pin *Kryova's
translation*: a millimetre becomes a metre exactly once, a prismatic driver is scaled and a
revolute one is not, a child is passed to `Initialize` before its parent, what the entry
point writes is exactly what `payload.from_result` reads. A stub agrees with whatever it
was written to agree with — the trap `app/ai/providers/ollama.py` fell into, which
CLAUDE.md names as "a mock of a wire format is a copy of what you believed it to be" — so
every *physical* claim was settled against the real `kryova-chrono:9.0.1` image instead,
and three of those runs found real defects that this file's stub had happily agreed with:

* **The solver.** Chrono's default iterative solver does not satisfy a revolute
  constraint here: a pendulum whose closed-form peak pivot reaction is 29.42 N reported
  **4286 N**, the rod stretching from 0.5 m to 0.74 m. With `SPARSE_QR` it reads 29.4156 N.
* **The reaction.** `GetReaction1` is the load on the child, which is Kryova's convention;
  the code took `GetReaction2`, the equal and opposite load on the parent. Same magnitude,
  reversed sign — so a magnitude check would have passed.
* **The frame.** Reaction 1 is expressed in frame 1, and the code rotated it by frame 2.
  The spun mass then reported a *constant* force vector while the exact evaluator had it
  sweeping round the circle, again at a magnitude agreeing to 0.004%.

The stub was updated to tell reaction 1 and 2 apart precisely so those cannot come back
silently. What no stub can prove is left in `docs/WINDOWS_VERIFICATION.md` G6.

One thing the stub *does* prove that no oracle run would: `_symbol` and `_call` resolve
both the Chrono 8 and the Chrono 9 spelling. A real build has one of them, so a real run
exercises one branch; the stub can be given either.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.dynamics.chrono import _entrypoint as entrypoint
from app.dynamics.chrono import payload as wire
from app.dynamics.chrono.engine import (
    POINT_MASS_NOTE,
    UNVERIFIED_NOTE,
    ContainerChronoEngine,
)
from app.dynamics.chrono.run import (
    ENTRYPOINT,
    ChronoUnavailable,
    availability,
    engine_identity,
    run_mechanism,
)
from app.dynamics.engine import engines, resolve
from app.dynamics.errors import MechanismError
from app.dynamics.types import Body, Driver, Joint, Mechanism, MotionRange

# -- a mechanism used throughout -----------------------------------------------


def _crank() -> Mechanism:
    """A driven crank and a slider on it: one revolute to ground, one prismatic on top.

    Chosen because it exercises every asymmetry at once — a driven revolute (radians,
    unscaled) *and* a driven prismatic (millimetres, scaled), a joint to ground *and* a
    joint to a body, a body with an inertia tensor *and* one without.
    """
    return Mechanism(
        name="crank",
        bodies=(
            Body(name="arm", mass_kg=2.0, centre_of_mass_mm=(50.0, 0.0, 0.0)),
            Body(name="slider", mass_kg=1.5, inertia_kg_mm2=(1000.0, 2000.0, 3000.0)),
        ),
        joints=(
            Joint(name="pivot", kind="revolute", body="arm"),
            Joint(
                name="slide",
                kind="prismatic",
                body="slider",
                parent="arm",
                origin_mm=(100.0, 0.0, 0.0),
                axis=(1.0, 0.0, 0.0),
            ),
        ),
        drivers=(
            Driver(joint="pivot", kind="constant", rate=10.0),
            Driver(joint="slide", kind="constant", offset=200.0, rate=50.0),
        ),
    )


def _range() -> MotionRange:
    return MotionRange(duration_s=0.1, samples=3)


# -- the stub ------------------------------------------------------------------


class _Vec:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Quat:
    """A quaternion, spelled the way Chrono spells one: e0 is the scalar part."""

    def __init__(self, w: float = 1.0, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.e0, self.e1, self.e2, self.e3 = w, x, y, z


class _Matrix33:
    def __init__(self) -> None:
        self.axes: tuple[Any, Any, Any] | None = None

    def SetFromDirectionAxes(self, x: Any, y: Any, z: Any) -> None:  # noqa: N802
        self.axes = (x, y, z)

    def GetQuaternion(self) -> _Quat:  # noqa: N802
        return _Quat()


class _Frame:
    def __init__(self, pos: Any = None, rot: Any = None) -> None:
        self.pos = pos if pos is not None else _Vec()
        self.rot = rot if rot is not None else _Quat()

    def GetPos(self) -> Any:  # noqa: N802
        return self.pos

    def TransformDirectionLocalToParent(self, v: Any) -> Any:  # noqa: N802
        return _Vec(v.x, v.y, v.z)


class _Wrench:
    def __init__(self, force: Any, torque: Any) -> None:
        self.force, self.torque = force, torque


class _Body:
    def __init__(self) -> None:
        self.mass = 0.0
        self.pos = _Vec()
        self.inertia: Any = None
        self.name = ""
        self.fixed = False
        self.vel = _Vec()
        self.acc = _Vec()

    def SetMass(self, m: float) -> None:  # noqa: N802
        self.mass = float(m)

    def SetPos(self, p: Any) -> None:  # noqa: N802
        self.pos = p

    def GetPos(self) -> Any:  # noqa: N802
        return self.pos

    def SetInertiaXX(self, i: Any) -> None:  # noqa: N802
        self.inertia = i

    def SetName(self, n: str) -> None:  # noqa: N802
        self.name = n

    def SetFixed(self, v: bool) -> None:  # noqa: N802
        self.fixed = v

    def GetRot(self) -> _Quat:  # noqa: N802
        """A quarter turn about z, so a wrong quaternion convention is visible."""
        return _Quat(math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4))

    def GetPosDt(self) -> Any:  # noqa: N802
        return self.vel

    def GetPosDt2(self) -> Any:  # noqa: N802
        return self.acc

    def GetAngVelLocal(self) -> Any:  # noqa: N802
        return _Vec(0.0, 0.0, 1.5)

    def GetAngAccLocal(self) -> Any:  # noqa: N802
        return _Vec(0.0, 0.0, 0.25)


class _Link:
    def __init__(self) -> None:
        self.name = ""
        self.init: tuple[Any, Any, Any] | None = None
        self.function: Any = None

    def Initialize(self, a: Any, b: Any, f: Any) -> None:  # noqa: N802
        self.init = (a, b, f)

    def SetName(self, n: str) -> None:  # noqa: N802
        self.name = n

    def GetReaction1(self) -> _Wrench:  # noqa: N802
        """The load on the CHILD — measured, see `_entrypoint._reaction`."""
        return _Wrench(_Vec(1.0, 2.0, 3.0), _Vec(0.4, 0.5, 0.6))

    def GetReaction2(self) -> _Wrench:  # noqa: N802
        """The equal and opposite load on the parent. Taking this one was the bug."""
        return _Wrench(_Vec(-1.0, -2.0, -3.0), _Vec(-0.4, -0.5, -0.6))

    def GetFrame1Abs(self) -> _Frame:  # noqa: N802
        return _Frame(_Vec(0.1, 0.2, 0.3))


class _RotationMotor(_Link):
    def SetAngleFunction(self, f: Any) -> None:  # noqa: N802
        self.function = f


class _LinearMotor(_Link):
    def SetPositionFunction(self, f: Any) -> None:  # noqa: N802
        self.function = f


class _Ramp:
    def __init__(self, y0: float, m: float) -> None:
        self.y0, self.m = y0, m


class _Sine:
    def __init__(self, a: float, f: float, p: float) -> None:
        self.a, self.f, self.p = a, f, p


class _Interp:
    def __init__(self) -> None:
        self.points: list[tuple[float, float]] = []

    def AddPoint(self, t: float, v: float) -> None:  # noqa: N802
        self.points.append((t, v))


class _Solver:
    """Chrono's solver-type enum, with the one name `_entrypoint.SOLVER_TYPE` asks for."""

    Type_SPARSE_QR = "sparse-qr"  # noqa: N815


class _System:
    #: Every system the stub has built, newest last. `simulate` builds its own, so this
    #: is how a test reaches the one that actually ran.
    built: list[_System] = []

    def __init__(self) -> None:
        _System.built.append(self)
        self.solver: Any = None
        self.t = 0.0
        self.bodies: list[Any] = []
        self.links: list[Any] = []
        self.gravity: Any = None
        self.steps: list[float] = []

    def SetSolverType(self, kind: Any) -> None:  # noqa: N802
        self.solver = kind

    def SetGravitationalAcceleration(self, g: Any) -> None:  # noqa: N802
        self.gravity = g

    def AddBody(self, b: Any) -> None:  # noqa: N802
        self.bodies.append(b)

    def AddLink(self, link: Any) -> None:  # noqa: N802
        self.links.append(link)

    def GetChTime(self) -> float:  # noqa: N802
        return self.t

    def DoStepDynamics(self, dt: float) -> None:  # noqa: N802
        """Advance time and slide every free body along +x at 0.5 m/s.

        Deliberately not physics: it is a *ramp with known numbers*, so every assertion
        below is about a unit conversion rather than about a solve. See the module
        docstring.
        """
        self.steps.append(dt)
        self.t += dt
        for body in self.bodies:
            if not body.fixed:
                body.pos = _Vec(body.pos.x + 0.5 * dt, body.pos.y, body.pos.z)
                body.vel = _Vec(0.5, 0.0, 0.0)
                body.acc = _Vec(0.25, 0.0, 0.0)


class _Chrono:
    """A PyChrono-shaped module spelled the Chrono 9 way."""

    __version__ = "9.0.1-stub"
    ChSolver = _Solver
    ChSystemNSC = _System
    ChBody = _Body
    ChVector3d = _Vec
    ChQuaterniond = _Quat
    ChFramed = _Frame
    ChMatrix33d = _Matrix33
    ChLinkLockRevolute = _Link
    ChLinkLockPrismatic = _Link
    ChLinkLockLock = _Link
    ChLinkMotorRotationAngle = _RotationMotor
    ChLinkMotorLinearPosition = _LinearMotor
    ChFunctionRamp = _Ramp
    ChFunctionSine = _Sine
    ChFunctionInterp = _Interp


class _Chrono8:
    """The same module spelled the Chrono 8 way, to prove `_call` resolves both."""

    __version__ = "8.0.0-stub"
    ChSolver = _Solver

    class _Body8(_Body):
        SetFixed = None  # type: ignore[assignment]
        GetPosDt = None  # type: ignore[assignment]
        GetPosDt2 = None  # type: ignore[assignment]

        def SetBodyFixed(self, v: bool) -> None:  # noqa: N802
            self.fixed = v

        def GetPos_dt(self) -> Any:  # noqa: N802
            return self.vel

        def GetPos_dtdt(self) -> Any:  # noqa: N802
            return self.acc

        def GetWvel_loc(self) -> Any:  # noqa: N802
            return _Vec(0.0, 0.0, 1.5)

        def GetWacc_loc(self) -> Any:  # noqa: N802
            return _Vec(0.0, 0.0, 0.25)

    class _System8(_System):
        SetGravitationalAcceleration = None  # type: ignore[assignment]

        def Set_G_acc(self, g: Any) -> None:  # noqa: N802
            self.gravity = g

    ChSystemNSC = _System8
    ChBody = _Body8
    ChVectorD = _Vec
    ChQuaternionD = _Quat
    ChFrameD = _Frame
    ChMatrix33D = _Matrix33
    ChLinkLockRevolute = _Link
    ChLinkLockPrismatic = _Link
    ChLinkLockLock = _Link
    ChLinkMotorRotationAngle = _RotationMotor
    ChLinkMotorLinearPosition = _LinearMotor
    ChFunction_Ramp = _Ramp
    ChFunction_Sine = _Sine
    ChFunction_Recorder = _Interp


def _spec() -> dict[str, Any]:
    """The crank as JSON, having been through `json` so nothing is shared by reference."""
    return json.loads(json.dumps(wire.to_payload(_crank(), _range())))


# -- the tests -----------------------------------------------------------------


class TestAMechanismCrossesTheWireInMillimetres:
    """What `to_payload` sends, and the one quantity that converts on this side."""

    def test_lengths_stay_in_millimetres(self) -> None:
        sent = wire.to_payload(_crank(), _range())
        assert sent["bodies"][0]["centre_of_mass_mm"] == [50.0, 0.0, 0.0]
        assert sent["joints"][1]["origin_mm"] == [100.0, 0.0, 0.0]
        assert sent["gravity_mm_s2"][2] == pytest.approx(-9806.65)

    def test_an_inertia_tensor_is_sent_and_converted_to_kg_m2(self) -> None:
        """The one conversion on this side of the wire. kg.mm2 is `types.Body`'s unit."""
        sent = wire.to_payload(_crank(), _range())
        slider = sent["bodies"][1]
        assert slider["has_inertia_tensor"] is True
        assert slider["inertia_kg_m2"] == [1e-3, 2e-3, 3e-3]

    def test_a_body_with_no_tensor_sends_none_and_says_so(self) -> None:
        """An early draft hard-coded False here and discarded a tensor the caller gave."""
        arm = wire.to_payload(_crank(), _range())["bodies"][0]
        assert arm["has_inertia_tensor"] is False
        assert "inertia_kg_m2" not in arm

    def test_the_payload_is_json(self) -> None:
        """It crosses as a file into another interpreter. Nothing may be pickled."""
        sent = wire.to_payload(_crank(), _range())
        assert json.loads(json.dumps(sent)) == sent

    def test_a_spherical_joint_is_refused_by_name(self) -> None:
        mechanism = Mechanism(
            name="ball",
            bodies=(Body(name="b", mass_kg=1.0),),
            joints=(Joint(name="j", kind="spherical", body="b"),),
        )
        with pytest.raises(MechanismError, match="spherical"):
            wire.to_payload(mechanism, _range())


class TestTheContainerIsSentSI:
    """`_build`: every length divided by 1000 exactly once, and nothing else touched."""

    def test_gravity_arrives_in_metres_per_second_squared(self) -> None:
        system, _, _, _ = entrypoint._build(_Chrono(), _spec())
        assert system.gravity.z == pytest.approx(-9.80665)

    def test_a_centre_of_mass_in_millimetres_arrives_in_metres(self) -> None:
        _, bodies, _, _ = entrypoint._build(_Chrono(), _spec())
        assert bodies["arm"].pos.x == pytest.approx(0.05)

    def test_a_joint_origin_in_millimetres_arrives_in_metres(self) -> None:
        _, _, links, _ = entrypoint._build(_Chrono(), _spec())
        assert links["slide"].init[2].pos.x == pytest.approx(0.1)

    def test_a_tensor_that_was_sent_is_used_and_one_that_was_not_is_substituted(self) -> None:
        """A zero tensor is a singular mass matrix, not a point mass."""
        _, bodies, _, _ = entrypoint._build(_Chrono(), _spec())
        assert bodies["slider"].inertia.x == pytest.approx(1e-3)
        assert bodies["arm"].inertia.x == pytest.approx(entrypoint.NEGLIGIBLE_INERTIA_KG_M2)

    def test_the_child_is_initialised_before_its_parent(self) -> None:
        """Swapping them builds a mechanism that runs and moves the wrong body."""
        _, bodies, links, _ = entrypoint._build(_Chrono(), _spec())
        assert links["slide"].init[0] is bodies["slider"]
        assert links["slide"].init[1] is bodies["arm"]

    def test_a_joint_to_ground_is_joined_to_the_fixed_body(self) -> None:
        system, _, links, _ = entrypoint._build(_Chrono(), _spec())
        ground = system.bodies[0]
        assert ground.fixed is True
        assert links["pivot"].init[1] is ground

    def test_a_driven_joint_becomes_a_motor_of_its_own_kind(self) -> None:
        _, _, links, _ = entrypoint._build(_Chrono(), _spec())
        assert isinstance(links["pivot"], _RotationMotor)
        assert isinstance(links["slide"], _LinearMotor)

    def test_a_prismatic_driver_is_scaled_and_a_revolute_one_is_not(self) -> None:
        """The asymmetry that makes a stroke 1000x wrong while looking plausible.

        A revolute's coordinate is radians on both sides; a prismatic's is millimetres
        here and metres there.
        """
        _, _, links, _ = entrypoint._build(_Chrono(), _spec())
        assert (links["pivot"].function.y0, links["pivot"].function.m) == (0.0, 10.0)
        assert links["slide"].function.y0 == pytest.approx(0.2)
        assert links["slide"].function.m == pytest.approx(0.05)


class TestWhatComesBackIsKryovasUnitsAgain:
    """`simulate`: metres back to millimetres, and newton-metres to newton-millimetres."""

    def test_the_samples_are_the_range_that_was_asked_for(self) -> None:
        out = entrypoint.simulate(_Chrono(), _spec())
        assert [round(t, 6) for t in out["times_s"]] == [0.0, 0.05, 0.1]

    def test_the_integrator_step_is_not_the_sampling_interval(self) -> None:
        """Otherwise asking for a smoother plot changes the answer."""
        entrypoint.simulate(_Chrono(), _spec())
        ran = _System.built[-1]
        assert ran.steps, "nothing was integrated"
        assert max(ran.steps) <= entrypoint.MAX_STEP_S

    def test_a_velocity_comes_back_in_millimetres_per_second(self) -> None:
        out = entrypoint.simulate(_Chrono(), _spec())
        assert out["bodies"]["arm"]["velocity_mm_s"][-1] == [500.0, 0.0, 0.0]

    def test_an_acceleration_comes_back_in_millimetres_per_second_squared(self) -> None:
        out = entrypoint.simulate(_Chrono(), _spec())
        assert out["bodies"]["arm"]["acceleration_mm_s2"][-1] == [250.0, 0.0, 0.0]

    def test_an_angular_rate_is_not_converted_at_all(self) -> None:
        """Radians are radians. Scaling one is the mistake a blanket conversion makes."""
        out = entrypoint.simulate(_Chrono(), _spec())
        assert out["bodies"]["arm"]["angular_velocity_rad_s"][-1] == [0.0, 0.0, 1.5]
        assert out["bodies"]["arm"]["angular_acceleration_rad_s2"][-1] == [0.0, 0.0, 0.25]

    def test_a_reaction_force_is_newtons_on_both_sides_and_a_moment_is_not(self) -> None:
        out = entrypoint.simulate(_Chrono(), _spec())
        assert out["reactions"]["pivot"]["force_n"][0] == [1.0, 2.0, 3.0]
        assert out["reactions"]["pivot"]["moment_n_mm"][0] == [400.0, 500.0, 600.0]
        assert out["reactions"]["pivot"]["location_mm"][0] == [100.0, 200.0, 300.0]

    def test_the_ground_body_is_not_reported_as_one_of_the_mechanisms(self) -> None:
        out = entrypoint.simulate(_Chrono(), _spec())
        assert set(out["bodies"]) == {"arm", "slider"}

    def test_a_quarter_turn_about_z_reads_as_a_quarter_turn_about_z(self) -> None:
        """The quaternion-to-matrix conversion, which is arithmetic this file owns."""
        out = entrypoint.simulate(_Chrono(), _spec())
        rows = out["bodies"]["arm"]["frame_rotation"][0]
        assert rows == pytest.approx([0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0], abs=1e-12)

    def test_a_pose_origin_and_a_centre_of_mass_are_one_reading(self) -> None:
        """True for a plain `ChBody`; `ChBodyAuxRef` is what separates them."""
        out = entrypoint.simulate(_Chrono(), _spec())
        arm = out["bodies"]["arm"]
        assert arm["frame_origin_mm"] == arm["position_mm"]

    def test_a_substituted_inertia_is_named_in_the_warnings(self) -> None:
        out = entrypoint.simulate(_Chrono(), _spec())
        assert any("arm" in w and "negligible" in w for w in out["warnings"])
        assert not any("slider" in w for w in out["warnings"])

    def test_the_reaction_read_is_the_load_on_the_child(self) -> None:
        """`GetReaction1`, not `GetReaction2`, and the stub makes the two tell apart.

        Measured on the real engine (2026-09-16): both have magnitude `m w^2 r` and they
        point opposite ways, so taking the wrong one publishes every joint load
        sign-reversed at exactly the right size. The stub returns `(1, 2, 3)` from
        reaction 1 and `(-1, -2, -3)` from reaction 2 for that reason.
        """
        out = entrypoint.simulate(_Chrono(), _spec())
        assert out["reactions"]["pivot"]["force_n"][0] == [1.0, 2.0, 3.0]

    def test_nothing_is_caveated_when_everything_resolved(self) -> None:
        """The convention and the frame are settled now, so there is no question to carry."""
        out = entrypoint.simulate(_Chrono(), _spec())
        assert out["reactions"]["pivot"]["moment_caveat"] == ""

    def test_a_build_with_no_direct_solver_says_the_numbers_are_unusable(self) -> None:
        """Loud, because the numbers stay plausible: 29.42 N becomes 4286 N.

        Measured 2026-09-16. Chrono's default iterative solver does not satisfy a
        revolute constraint on this class of model, and nothing in the result itself
        would tell the two apart.
        """

        class _NoSolverChoice(_Chrono):
            ChSolver = None

        out = entrypoint.simulate(_NoSolverChoice(), _spec())
        assert any("4286" in w for w in out["warnings"])
        assert "default iterative" in out["method"]

    def test_a_direct_solver_is_named_in_the_method(self) -> None:
        out = entrypoint.simulate(_Chrono(), _spec())
        assert entrypoint.SOLVER_TYPE in out["method"]

    def test_the_first_sample_is_flagged_as_unmeasured_rather_than_zero(self) -> None:
        """Chrono forms no constraint force until it has stepped. Zero is not 'no load'."""

        class _QuietRotation(_RotationMotor):
            def GetReaction1(self) -> _Wrench:  # noqa: N802
                return _Wrench(_Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))

        class _QuietLinear(_LinearMotor):
            def GetReaction1(self) -> _Wrench:  # noqa: N802
                return _Wrench(_Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))

        class _Build(_Chrono):
            ChLinkMotorRotationAngle = _QuietRotation
            ChLinkMotorLinearPosition = _QuietLinear

        out = entrypoint.simulate(_Build(), _spec())
        assert any("t = 0" in w and "unmeasured" in w for w in out["warnings"])

    def test_a_build_with_no_readable_rotation_says_so_rather_than_sending_the_identity(
        self,
    ) -> None:
        """A silent identity is a body that never turns, which looks like one that does not."""

        class _NoRotation(_Body):
            GetRot = None  # type: ignore[assignment]

        class _Build(_Chrono):
            ChBody = _NoRotation

        out = entrypoint.simulate(_Build(), _spec())
        assert out["bodies"]["arm"]["frame_rotation"][0] == []
        assert any("no readable body rotation" in w for w in out["warnings"])


class TestBothChronoSpellingsResolve:
    """Chrono 9 renamed a great deal. A real build exercises one branch; this does both."""

    def test_the_chrono_8_spelling_runs_the_same_translation(self) -> None:
        out = entrypoint.simulate(_Chrono8(), _spec())
        assert out["bodies"]["arm"]["velocity_mm_s"][-1] == [500.0, 0.0, 0.0]
        assert _System.built[-1].gravity.z == pytest.approx(-9.80665)

    def test_a_missing_symbol_is_refused_naming_every_spelling_tried(self) -> None:
        """Rather than an AttributeError from deep inside, which reads as a Chrono bug."""
        with pytest.raises(RuntimeError, match="ChVector3d.*ChVectorD"):
            entrypoint._symbol(object(), "ChVector3d", "ChVectorD")

    def test_a_missing_method_is_refused_naming_every_spelling_tried(self) -> None:
        with pytest.raises(RuntimeError, match="GetPosDt.*GetPos_dt"):
            entrypoint._call(object(), ("GetPosDt", "GetPos_dt"))

    def test_an_optional_reading_that_is_absent_is_none_rather_than_an_error(self) -> None:
        assert entrypoint._maybe(object(), ("GetFrame1Abs",)) is None
        assert entrypoint._maybe(None, ("GetPos",)) is None


class TestADriverBecomesATimeFunction:
    def test_a_constant_driver_is_a_ramp(self) -> None:
        made = entrypoint._driver_function(_Chrono(), {"kind": "constant", "offset": 2.0, "rate": 3.0})
        assert (made.y0, made.m) == (2.0, 3.0)

    def test_a_harmonic_driver_passes_hertz_and_radians_through_unconverted(self) -> None:
        made = entrypoint._driver_function(
            _Chrono(),
            {"kind": "harmonic", "offset": 0.0, "amplitude": 1.0, "frequency_hz": 2.0, "phase_rad": 0.3},
        )
        assert (made.a, made.f, made.p) == (1.0, 2.0, 0.3)

    def test_a_harmonic_driver_with_an_offset_is_refused_rather_than_dropped(self) -> None:
        """Chrono's sine carries no offset term, and a dropped one moves the whole motion."""
        with pytest.raises(RuntimeError, match="offset"):
            entrypoint._driver_function(
                _Chrono(), {"kind": "harmonic", "offset": 5.0, "amplitude": 1.0, "frequency_hz": 2.0}
            )

    def test_a_table_driver_keeps_its_points_in_order(self) -> None:
        made = entrypoint._driver_function(
            _Chrono(), {"kind": "table", "times_s": [0.0, 1.0], "values": [0.0, 5.0]}
        )
        assert made.points == [(0.0, 0.0), (1.0, 5.0)]

    def test_a_ragged_table_is_refused(self) -> None:
        with pytest.raises(RuntimeError, match="same number of times and values"):
            entrypoint._driver_function(
                _Chrono(), {"kind": "table", "times_s": [0.0, 1.0], "values": [0.0]}
            )

    def test_an_unknown_driver_kind_is_refused_by_name(self) -> None:
        with pytest.raises(RuntimeError, match="wobble"):
            entrypoint._driver_function(_Chrono(), {"kind": "wobble"})

    def test_scaling_a_driver_scales_every_linear_parameter_and_no_other(self) -> None:
        """Why scaling the parameters is exactly scaling the function: each kind is linear."""
        scaled = entrypoint._scaled(
            {"offset": 100.0, "rate": 50.0, "amplitude": 2.0, "frequency_hz": 5.0, "values": [1.0]},
            1e-3,
        )
        assert scaled["offset"] == pytest.approx(0.1)
        assert scaled["rate"] == pytest.approx(0.05)
        assert scaled["amplitude"] == pytest.approx(2e-3)
        assert scaled["values"] == [1e-3]
        assert scaled["frequency_hz"] == 5.0, "a frequency is not a length"

    def test_a_joint_with_no_single_coordinate_cannot_be_driven(self) -> None:
        with pytest.raises(RuntimeError, match="cannot be driven"):
            entrypoint._make_motor(_Chrono(), "lock")

    def test_an_axis_of_zero_length_is_refused(self) -> None:
        with pytest.raises(RuntimeError, match="zero length"):
            entrypoint._unit([0.0, 0.0, 0.0])


class TestAResultChronoWroteIsReadBackWhole:
    """The round trip: what the entry point writes is exactly what `from_result` reads.

    The two files were written separately and never import each other — this is the only
    thing that holds their two vocabularies to one shape.
    """

    @staticmethod
    def _round_trip() -> Any:
        mechanism = _crank()
        produced = entrypoint.simulate(_Chrono(), _spec())
        return wire.from_result(
            mechanism, json.loads(json.dumps(produced)), engine="chrono-container"
        )

    def test_every_body_and_every_joint_survives(self) -> None:
        result = self._round_trip()
        assert sorted(result.path.bodies) == ["arm", "slider"]
        assert sorted(result.reactions) == ["pivot", "slide"]

    def test_the_engine_names_itself_on_the_result(self) -> None:
        result = self._round_trip()
        assert result.engine == "chrono-container"
        assert result.mechanism == "crank"

    def test_a_pose_arrives_as_a_frame_with_its_rotation(self) -> None:
        result = self._round_trip()
        frames = result.path.motion("arm").frames
        assert len(frames) == 3
        assert frames[0].rotation == pytest.approx(
            (0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0), abs=1e-12
        )
        assert frames[-1].origin_mm == pytest.approx((100.0, 0.0, 0.0))

    def test_the_derived_readings_are_computable_from_it(self) -> None:
        """The point of reading it back into the vocabulary: everything downstream works."""
        result = self._round_trip()
        assert result.path.motion("arm").peak_acceleration_mm_s2 == pytest.approx(250.0)
        assert result.reaction("pivot").available is True
        assert result.reaction("pivot").peak_force_n == pytest.approx(math.sqrt(14.0))

    def test_the_caveats_travel_with_the_numbers(self) -> None:
        result = self._round_trip()
        assert any("negligible" in w for w in result.warnings)


class TestTheWireRefusesWhatItCannotRead:
    """`from_result` validates rather than trusts: the file came from another process."""

    def test_an_image_speaking_another_wire_version_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="wire version"):
            wire.from_result(_crank(), {"wire_version": 99}, engine="x")

    def test_a_result_with_no_samples_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="no time samples"):
            wire.from_result(_crank(), {"wire_version": 1, "times_s": []}, engine="x")

    def test_an_array_shorter_than_the_times_is_refused_by_name(self) -> None:
        """A short array zipped against a long one is the right-looking wrong answer."""
        written = json.loads(json.dumps(entrypoint.simulate(_Chrono(), _spec())))
        written["bodies"]["arm"]["velocity_mm_s"] = written["bodies"]["arm"]["velocity_mm_s"][:1]
        with pytest.raises(MechanismError, match="velocity_mm_s"):
            wire.from_result(_crank(), written, engine="x")

    def test_a_malformed_vector_is_refused(self) -> None:
        written = json.loads(json.dumps(entrypoint.simulate(_Chrono(), _spec())))
        written["bodies"]["arm"]["position_mm"][0] = [1.0, 2.0]
        with pytest.raises(MechanismError, match="malformed vector"):
            wire.from_result(_crank(), written, engine="x")

    def test_a_malformed_rotation_is_refused_saying_what_one_is(self) -> None:
        written = json.loads(json.dumps(entrypoint.simulate(_Chrono(), _spec())))
        written["bodies"]["arm"]["frame_rotation"][0] = [1.0, 0.0]
        with pytest.raises(MechanismError, match="nine numbers"):
            wire.from_result(_crank(), written, engine="x")


class TestNotInstalledIsNotFailed:
    """Install something, versus ask something else — two different recoveries."""

    def test_an_unknown_launcher(self) -> None:
        assert "must be one of docker, local" in (availability("kubernetes") or "")

    def test_no_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: None)
        assert "no `docker` executable" in (availability("docker") or "")

    def test_the_image_is_never_built_during_a_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A run that installs its own solver has a version nobody chose."""
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.dynamics.chrono.run._image_present", lambda image: False)
        reason = availability("docker", "kryova-chrono:9.0.1") or ""
        assert "scripts/chrono_image.sh" in reason
        assert "no published image to pull" in reason

    def test_a_run_with_no_launcher_refuses_before_it_writes_anything(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: None)
        with pytest.raises(ChronoUnavailable, match="docker"):
            run_mechanism(tmp_path, _spec(), launcher="docker")
        assert list(tmp_path.iterdir()) == []


class TestTheEngineIsNamedByItsBytes:
    """What a job cache may key on. A tag can be rebuilt; the content id cannot."""

    @staticmethod
    def _inspect(stdout: str, returncode: int = 0) -> Any:
        return lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode, stdout=stdout, stderr=""
        )

    def test_an_image_is_named_by_tag_and_content_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.dynamics.chrono.run.subprocess.run", self._inspect("sha256:abc123\n"))
        assert engine_identity("docker", "kryova-chrono:9.0.1") == (
            "docker kryova-chrono:9.0.1 sha256:abc123"
        )

    def test_something_that_is_not_a_content_id_is_not_taken_for_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.dynamics.chrono.run.subprocess.run", self._inspect("kryova-chrono:9.0.1\n"))
        assert engine_identity("docker", "kryova-chrono:9.0.1") is None

    def test_a_local_install_cannot_be_named_before_it_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Which the job cache reads as 'never reuse' — Decision 3."""
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: "/usr/bin/python")
        assert engine_identity("local") is None


class TestTheBoundaryIsAFileAndNothingElse:
    """One directory: `input.json` in, the entry point beside it, `output.json` out."""

    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list[list[str]]:
        seen: list[list[str]] = []

        def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            seen.append(command)
            return subprocess.CompletedProcess(command, returncode, stdout="", stderr="")

        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.dynamics.chrono.run._image_present", lambda image: True)
        monkeypatch.setattr("app.dynamics.chrono.run.subprocess.run", _run)
        return seen

    def test_the_input_and_the_entry_point_are_written_into_the_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._spy(monkeypatch)
        (tmp_path / "output.json").write_text(
            json.dumps({"wire_version": 1, "times_s": [0.0]}), encoding="utf-8"
        )
        run_mechanism(tmp_path, _spec(), launcher="docker")
        assert json.loads((tmp_path / "input.json").read_text(encoding="utf-8"))["name"] == "crank"
        assert (tmp_path / ENTRYPOINT).is_file()

    def test_the_entry_point_is_written_with_unix_line_endings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """It is read by a Linux interpreter inside the container, whatever wrote it."""
        self._spy(monkeypatch)
        (tmp_path / "output.json").write_text(
            json.dumps({"wire_version": 1, "times_s": [0.0]}), encoding="utf-8"
        )
        run_mechanism(tmp_path, _spec(), launcher="docker")
        assert b"\r\n" not in (tmp_path / ENTRYPOINT).read_bytes()

    def test_the_container_runs_as_the_calling_user_with_no_network(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A work directory the server cannot delete is a disk that fills."""
        seen = self._spy(monkeypatch)
        (tmp_path / "output.json").write_text(
            json.dumps({"wire_version": 1, "times_s": [0.0]}), encoding="utf-8"
        )
        run_mechanism(tmp_path, _spec(), launcher="docker")
        command = seen[0]
        assert command[:3] == ["docker", "run", "--rm"]
        assert "--network" in command and command[command.index("--network") + 1] == "none"
        assert "--user" in command
        assert command[-1] == f"/work/{ENTRYPOINT}"

    def test_a_container_that_wrote_nothing_is_a_failure_quoting_what_it_said(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._spy(monkeypatch, returncode=1)
        with pytest.raises(MechanismError, match="did not produce a result"):
            run_mechanism(tmp_path, _spec(), launcher="docker")

    def test_an_error_the_entry_point_reported_is_raised_rather_than_read_as_a_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._spy(monkeypatch)
        (tmp_path / "output.json").write_text(
            json.dumps({"wire_version": 1, "error": "RuntimeError: no such joint kind"}),
            encoding="utf-8",
        )
        with pytest.raises(MechanismError, match="no such joint kind"):
            run_mechanism(tmp_path, _spec(), launcher="docker")

    def test_a_run_that_does_not_stop_is_stopped_and_names_the_levers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def _timeout(command: list[str], **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(command, 1.0)

        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.dynamics.chrono.run._image_present", lambda image: True)
        monkeypatch.setattr("app.dynamics.chrono.run.subprocess.run", _timeout)
        with pytest.raises(MechanismError, match="raise step_s"):
            run_mechanism(tmp_path, _spec(), launcher="docker", timeout_s=1.0)


class TestTheEntryPointIsAlone:
    """It runs under the container's Python, where `app` does not exist."""

    @staticmethod
    def _source() -> str:
        return (Path(entrypoint.__file__)).read_text(encoding="utf-8")

    def test_it_imports_nothing_from_this_application(self) -> None:
        source = self._source()
        assert "from app" not in source
        assert "import app" not in source

    def test_it_imports_nothing_outside_the_standard_library(self) -> None:
        """`pychrono` is imported inside `main`, because it only exists in the container."""
        for line in self._source().splitlines():
            if line.startswith(("import ", "from ")):
                module = line.split()[1].split(".")[0]
                assert module in {"__future__", "json", "math", "sys", "traceback", "pathlib", "typing"}, line

    def test_run_ships_the_file_that_is_in_this_repository(self) -> None:
        """Rather than one baked into the image, so a result's provenance is this commit."""
        from app.dynamics.chrono.run import _entrypoint_source

        assert _entrypoint_source() == self._source()


class TestAnUnverifiedEngineDoesNotWinAFallThrough:
    """E9.1's honest status, enforced by the order `engines()` returns.

    The container engine covers strictly more than the kinematic one and is deliberately
    behind it, because it has never been checked against a closed-form answer here.
    """

    def test_the_container_engine_is_known_to_the_seam(self) -> None:
        assert "chrono-container" in {engine.name for engine in engines()}

    def test_the_exact_evaluator_is_reached_before_the_integrator(self) -> None:
        order = [engine.name for engine in engines()]
        assert order.index("kinematic") < order.index("chrono-container")

    def test_resolving_with_no_name_never_returns_it(self) -> None:
        assert resolve().name == "kinematic"

    def test_it_can_still_be_asked_for_by_name(self) -> None:
        """A refusal naming the image is the answer on a machine with no image built."""
        from app.dynamics.errors import EngineUnavailable

        try:
            engine = resolve("chrono-container")
        except EngineUnavailable as refusal:
            assert "chrono-container" in str(refusal)
        else:
            assert engine.name == "chrono-container"

    def test_the_in_process_route_is_still_shut_and_is_a_different_engine(self) -> None:
        """`ChronoEngine`'s refusal is about pip, and it is still true."""
        names = [engine.name for engine in engines()]
        assert "chrono" in names and "chrono-container" in names

    def test_an_unavailable_engine_says_what_to_do_about_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: None)
        status = ContainerChronoEngine(launcher="docker").availability()
        assert status.available is False
        assert "docker" in status.reason

    def test_an_available_engine_is_identified_by_the_images_content_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.dynamics.chrono.run.shutil.which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr("app.dynamics.chrono.run._image_present", lambda image: True)
        monkeypatch.setattr(
            "app.dynamics.chrono.run.image_id", lambda image: "sha256:deadbeef"
        )
        status = ContainerChronoEngine(launcher="docker", image="kryova-chrono:9.0.1").availability()
        assert status.available is True
        assert status.version == "docker kryova-chrono:9.0.1 sha256:deadbeef"


class TestEveryResultSaysItIsUnverified:
    """A caveat only a developer reads is not a caveat on a result."""

    def test_the_notes_travel_on_the_result_not_only_in_a_docstring(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written = json.loads(json.dumps(entrypoint.simulate(_Chrono(), _spec())))
        monkeypatch.setattr(
            "app.dynamics.chrono.engine.availability", lambda launcher, image: None
        )
        monkeypatch.setattr(
            "app.dynamics.chrono.engine.engine_identity",
            lambda launcher, image: "docker kryova-chrono:9.0.1 sha256:abc",
        )
        monkeypatch.setattr(
            "app.dynamics.chrono.engine.run_mechanism",
            lambda directory, body, **kwargs: (None, written),
        )
        engine = ContainerChronoEngine(launcher="docker")
        result = engine.simulate(_crank(), _range())
        assert UNVERIFIED_NOTE in result.warnings
        assert POINT_MASS_NOTE in result.warnings
        assert result.engine == "chrono-container"

    def test_the_unverified_note_names_where_the_oracle_runs_are_recorded(self) -> None:
        assert "WINDOWS_VERIFICATION" in UNVERIFIED_NOTE
