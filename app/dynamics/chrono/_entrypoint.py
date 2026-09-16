"""Runs INSIDE the Chrono container. Reads `input.json`, writes `output.json`.

**This file never imports `app` and is never imported by it.** It is copied into the work
directory and executed by the container's own Python — a different interpreter, a
different environment, no Kryova on the path. `run.py` ships it per run rather than baking
it into the image so the translation that executes is the one in this repository at this
commit, which is what a result's provenance has to be able to say.

Standard library plus `pychrono`, for the reason `app/verify/corpora.py` is standard
library only: the container's environment is not this venv and never will be.

**Units convert here, exactly once each.** Kryova is mm-N-MPa throughout and Chrono is SI.
Lengths divide by 1000 going in and multiply by 1000 coming out; forces are newtons on
both sides and do not convert; a moment is N·m in Chrono and N·mm here, so it multiplies
by 1000. Inertia is the one quantity that converts on the *other* side of the wire, in
`payload.py`, because kg·mm² is the unit `types.Body` names. Every length conversion goes
through `_mm_to_m` or `_m_to_mm` — converting anywhere else would convert twice, and a
load 1000× wrong looks entirely plausible on a drawing.

**A driver's unit is scaled into its own parameters, not wrapped around its function.**
Every driver kind here — constant, harmonic, table — is linear in `offset`, `rate`,
`amplitude` and `values`, so multiplying those by 1/1000 for a prismatic joint *is*
multiplying the function by 1/1000, exactly. The first draft of this file composed a
Chrono function-operator around the driver instead, which needed three API names that
differ by version to express something arithmetic already says.

**Symbols are resolved rather than spelled.** Chrono 9.0 renamed a great deal of its API
(`ChVectorD` → `ChVector3d`, `ChFunction_Const` → `ChFunctionConst`, `SetBodyFixed` →
`SetFixed`, `Set_G_acc` → `SetGravitationalAcceleration`, and more) and both spellings are
in circulation in documentation and examples. `_symbol` and `_call` take the alternatives
and **fail naming every one tried** rather than letting an `AttributeError` surface from
somewhere deep in the translation, where it would read as a Chrono bug rather than as a
version mismatch.

**A rotation is read as a quaternion and converted here, in arithmetic.** `GetRot` is one
of the few names Chrono has not moved, and the quaternion→matrix formula is something this
file can be held to. Reading a `ChMatrix33`'s elements is not: how a SWIG-wrapped Eigen
matrix indexes from Python is exactly the kind of thing that differs by build, and getting
it wrong would fill `frames` with plausible nonsense rather than raising.

**Three things were measured against the real engine on 2026-09-16** (image
`kryova-chrono:9.0.1`), and each was written the other way first — so the comments naming
them are the record of a wrong guess corrected, not decoration:

* **The solver must be direct.** See `SOLVER_TYPE`. Chrono's default iterative solver
  does not satisfy a revolute constraint here, and reports a pendulum reaction 146x too
  large while looking like an ordinary number.
* **`GetReaction1` is the load on the child**, which is Kryova's convention;
  `GetReaction2` is the equal and opposite load on the parent. Both have the right
  magnitude, so taking the wrong one publishes every joint load sign-reversed at exactly
  the right size. See `_reaction`.
* **A reaction arrives in the joint's frame** and is rotated into world coordinates
  through the link's absolute frame. Where that rotation cannot be obtained the raw value
  comes back with `JOINT_FRAME_CAVEAT` — never silently as though it were world.

And one trap that is about the binding rather than the physics: **never chain
`link.GetReaction1().force`**. The wrench is a temporary; freed at the end of the
expression, the vector read out of it is garbage (`-4.86e188`, measured). Bind it first.
"""

from __future__ import annotations

import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any

WIRE_VERSION = 1

#: A body with no inertia tensor cannot be sent to a rigid-body integrator as zero: a zero
#: tensor is a singular mass matrix, not a point mass. This is small enough to be
#: negligible against any real body and large enough to keep the solve conditioned, and
#: every result that used it says so in `warnings`.
NEGLIGIBLE_INERTIA_KG_M2 = 1e-6

#: The integrator takes at least this many steps between two reported samples. The samples
#: are what the caller asked to *see*; they must not also decide what the integrator was
#: allowed to *do*, or the answer changes when somebody asks for a smoother plot.
STEPS_PER_SAMPLE = 20

#: And never a step longer than this, however coarse the sampling.
MAX_STEP_S = 1e-3

#: **A direct solver, and this is not a preference — it is the difference between an
#: answer and noise.** Measured 2026-09-16 in `kryova-chrono:9.0.1`, a 1 kg point mass on
#: a 0.5 m revolute released from horizontal: with `SPARSE_QR` the peak pivot reaction is
#: **29.4199 N** against the closed-form 3mg = 29.4200 N and the radius holds 0.500000; with
#: Chrono's default iterative solver (PSOR) the same model reports **4286.1 N** — 146x too
#: large — and the radius drifts to 0.7359, because the constraint is never satisfied. A
#: mechanism is a handful of bodies, so a direct factorisation is cheap, and it removes an
#: iteration count that would otherwise silently decide whether a load case is real.
#: `APGD` was also measured and also drifts; `BARZILAIBORWEIN` and `MINRES` happened to
#: converge on the spin test and are not relied on.
SOLVER_TYPE = "SPARSE_QR"


def _symbol(module: Any, *names: str) -> Any:
    """The first of `names` this build has, or a refusal naming every one tried."""
    for name in names:
        found = getattr(module, name, None)
        if found is not None:
            return found
    raise RuntimeError(
        "This PyChrono build has none of " + ", ".join(names) + ". Kryova's translation "
        "supports the Chrono 8 and Chrono 9 spellings; this is neither, so the image "
        "needs rebuilding against a supported version."
    )


def _call(obj: Any, names: tuple[str, ...], *args: Any) -> Any:
    """Call the first method of `names` that exists, or refuse naming all of them."""
    for name in names:
        method = getattr(obj, name, None)
        if method is not None:
            return method(*args)
    raise RuntimeError(
        f"{type(obj).__name__} has none of {', '.join(names)} on this PyChrono build. "
        "Kryova's translation supports the Chrono 8 and Chrono 9 spellings."
    )


def _maybe(obj: Any, names: tuple[str, ...], *args: Any) -> Any:
    """`_call`, but None when this build has none of the names. For optional readings."""
    if obj is None:
        return None
    for name in names:
        method = getattr(obj, name, None)
        if method is not None:
            try:
                return method(*args)
            except Exception:  # noqa: BLE001 -- an optional reading is never fatal
                return None
    return None


def _mm_to_m(value: float) -> float:
    return value / 1000.0


def _m_to_mm(value: float) -> float:
    return value * 1000.0


def _xyz(vector: Any) -> list[float]:
    """A Chrono vector as three plain floats, in whatever unit it already was."""
    return [float(vector.x), float(vector.y), float(vector.z)]


def _vec(chrono: Any, x: float, y: float, z: float) -> Any:
    maker = _symbol(chrono, "ChVector3d", "ChVectorD")
    return maker(float(x), float(y), float(z))


def _vec_mm(chrono: Any, triple: Any) -> Any:
    """A Kryova millimetre triple as a Chrono vector in metres."""
    return _vec(chrono, _mm_to_m(triple[0]), _mm_to_m(triple[1]), _mm_to_m(triple[2]))


def _unit(triple: Any) -> tuple[float, float, float]:
    x, y, z = (float(v) for v in triple)
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0.0:
        raise RuntimeError(
            "A joint axis of zero length has no direction. Give the joint an axis, "
            "e.g. axis=(0, 0, 1)."
        )
    return (x / length, y / length, z / length)


def _axis_rotation(chrono: Any, axis: tuple[float, float, float]) -> Any:
    """A rotation taking +z onto `axis`.

    Built from an explicit orthonormal basis rather than from a library helper, because
    the helpers differ by version in which axis they align, and a silently different
    convention builds a joint about the wrong direction with nothing raising. Chrono's
    lock-family joints act about their frame's **z**, so this is what puts a revolute
    where the mechanism says it is.
    """
    z = axis
    seed = (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0)
    x = (
        seed[1] * z[2] - seed[2] * z[1],
        seed[2] * z[0] - seed[0] * z[2],
        seed[0] * z[1] - seed[1] * z[0],
    )
    norm = math.sqrt(sum(v * v for v in x))
    if norm == 0.0:  # pragma: no cover -- the seed choice above prevents it
        raise RuntimeError("Could not build a frame for the joint axis.")
    x = (x[0] / norm, x[1] / norm, x[2] / norm)
    y = (
        z[1] * x[2] - z[2] * x[1],
        z[2] * x[0] - z[0] * x[2],
        z[0] * x[1] - z[1] * x[0],
    )
    matrix = _symbol(chrono, "ChMatrix33d", "ChMatrix33D")()
    _call(
        matrix,
        ("SetFromDirectionAxes", "Set_A_axis"),
        _vec(chrono, *x),
        _vec(chrono, *y),
        _vec(chrono, *z),
    )
    return _call(matrix, ("GetQuaternion", "Get_A_quaternion"))


def _rotation_rows(quaternion: Any) -> list[float]:
    """A Chrono quaternion as nine row-major floats, or `[]` when it cannot be read.

    `[]` rather than the identity, and the caller turns that into a warning:
    `payload._frames` reads an absent rotation as the identity, and a *silent* identity is
    a body that never turns — which looks exactly like a body that does not turn.
    """
    if quaternion is None:
        return []
    try:
        w = float(quaternion.e0)
        x = float(quaternion.e1)
        y = float(quaternion.e2)
        z = float(quaternion.e3)
    except (AttributeError, TypeError, ValueError):
        return []
    return [
        1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w),
        2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w),
        2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y),
    ]


def _scaled(driver: dict[str, Any], scale: float) -> dict[str, Any]:
    """The driver with its coordinate scaled. See the module docstring for why this works."""
    if scale == 1.0:
        return driver
    scaled = dict(driver)
    for key in ("offset", "rate", "amplitude"):
        scaled[key] = float(driver.get(key) or 0.0) * scale
    scaled["values"] = [float(v) * scale for v in driver.get("values") or []]
    return scaled


def _driver_function(chrono: Any, driver: dict[str, Any]) -> Any:
    """A Kryova driver as a Chrono time function, already in SI."""
    kind = driver.get("kind", "constant")

    if kind == "constant":
        # q = offset + rate*t is a ramp; Chrono's ramp is y = y0 + m*x.
        ramp = _symbol(chrono, "ChFunctionRamp", "ChFunction_Ramp")
        return ramp(float(driver.get("offset") or 0.0), float(driver.get("rate") or 0.0))

    if kind == "harmonic":
        # Kryova: q = offset + A*sin(2*pi*f*t + phase). Chrono's sine carries no offset
        # term, and a dropped offset would move the whole motion with nothing saying so,
        # so a non-zero offset is refused by name rather than approximated. Expressing it
        # as a sum needs a function-operator class whose spelling differs by version, and
        # refusing is the honest answer until an oracle run can check one.
        offset = float(driver.get("offset") or 0.0)
        if offset != 0.0:
            raise RuntimeError(
                "A harmonic driver with a non-zero offset is not translated: Chrono's "
                "sine function carries no offset term, and dropping it would shift the "
                "whole motion silently. Put the offset in the joint's origin, or drive "
                "with offset 0."
            )
        sine = _symbol(chrono, "ChFunctionSine", "ChFunction_Sine")
        # Chrono's sine takes (amplitude, frequency in Hz, phase in radians), which is
        # Kryova's own spelling of a harmonic, so nothing is converted.
        return sine(
            float(driver.get("amplitude") or 0.0),
            float(driver.get("frequency_hz") or 0.0),
            float(driver.get("phase_rad") or 0.0),
        )

    if kind == "table":
        interp = _symbol(chrono, "ChFunctionInterp", "ChFunction_Recorder")
        table = interp()
        times = list(driver.get("times_s") or [])
        values = list(driver.get("values") or [])
        if not times or len(times) != len(values):
            raise RuntimeError(
                "A table driver needs the same number of times and values, and at least "
                f"one of each; got {len(times)} and {len(values)}."
            )
        for t, v in zip(times, values):
            _call(table, ("AddPoint",), float(t), float(v))
        return table

    raise RuntimeError(
        f"Unknown driver kind {kind!r}. Kryova sends constant, harmonic or table."
    )


def _make_motor(chrono: Any, kind: str) -> Any:
    if kind == "revolute":
        return _symbol(chrono, "ChLinkMotorRotationAngle")()
    if kind == "prismatic":
        return _symbol(chrono, "ChLinkMotorLinearPosition")()
    raise RuntimeError(
        f"A {kind} joint has no single coordinate a driver can prescribe, so it cannot "
        "be driven. Drive a revolute or a prismatic."
    )


def _make_link(chrono: Any, kind: str) -> Any:
    if kind == "revolute":
        return _symbol(chrono, "ChLinkLockRevolute")()
    if kind == "prismatic":
        return _symbol(chrono, "ChLinkLockPrismatic")()
    if kind == "lock":
        return _symbol(chrono, "ChLinkLockLock")()
    raise RuntimeError(f"Unknown joint kind {kind!r}.")


def _use_direct_solver(chrono: Any, system: Any) -> bool:
    """Ask for `SOLVER_TYPE`. True when this build had it. See that constant."""
    solver_enum = getattr(chrono, "ChSolver", None)
    wanted = getattr(solver_enum, f"Type_{SOLVER_TYPE}", None) if solver_enum else None
    setter = getattr(system, "SetSolverType", None)
    if wanted is None or setter is None:
        return False
    setter(wanted)
    return True


def _build(chrono: Any, spec: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any], bool]:
    """The Chrono system, its bodies by name, its links by name, and whether it is direct."""
    system = _symbol(chrono, "ChSystemNSC")()
    direct = _use_direct_solver(chrono, system)
    gravity = spec.get("gravity_mm_s2") or [0.0, 0.0, -9806.65]
    _call(
        system,
        ("SetGravitationalAcceleration", "Set_G_acc"),
        _vec_mm(chrono, list(gravity)),
    )

    ground = _symbol(chrono, "ChBody")()
    _call(ground, ("SetFixed", "SetBodyFixed"), True)
    _call(ground, ("SetName", "SetNameString"), "__ground__")
    _call(system, ("AddBody", "Add"), ground)

    bodies: dict[str, Any] = {}
    for entry in spec.get("bodies", []):
        body = _symbol(chrono, "ChBody")()
        body.SetMass(float(entry["mass_kg"]))
        body.SetPos(_vec_mm(chrono, list(entry.get("centre_of_mass_mm") or [0, 0, 0])))
        tensor = entry.get("inertia_kg_m2")
        if entry.get("has_inertia_tensor") and tensor:
            body.SetInertiaXX(_vec(chrono, tensor[0], tensor[1], tensor[2]))
        else:
            body.SetInertiaXX(
                _vec(
                    chrono,
                    NEGLIGIBLE_INERTIA_KG_M2,
                    NEGLIGIBLE_INERTIA_KG_M2,
                    NEGLIGIBLE_INERTIA_KG_M2,
                )
            )
        _call(body, ("SetName", "SetNameString"), str(entry["name"]))
        _call(system, ("AddBody", "Add"), body)
        bodies[str(entry["name"])] = body

    frame_type = _symbol(chrono, "ChFramed", "ChFrameD")
    drivers = {str(d["joint"]): d for d in spec.get("drivers", [])}

    links: dict[str, Any] = {}
    for entry in spec.get("joints", []):
        name = str(entry["name"])
        kind = str(entry["kind"])
        child = bodies[str(entry["body"])]
        parent = bodies[str(entry["parent"])] if entry.get("parent") else ground
        origin = _vec_mm(chrono, list(entry.get("origin_mm") or [0, 0, 0]))
        axis = _unit(list(entry.get("axis") or [0, 0, 1]))
        frame = frame_type(origin, _axis_rotation(chrono, axis))

        if name in drivers:
            # A prismatic driver's coordinate is millimetres and Chrono wants metres; a
            # revolute's is radians on both sides.
            scale = 1.0 / 1000.0 if kind == "prismatic" else 1.0
            link = _make_motor(chrono, kind)
            link.Initialize(child, parent, frame)
            _call(
                link,
                ("SetAngleFunction", "SetPositionFunction", "SetMotorFunction"),
                _driver_function(chrono, _scaled(drivers[name], scale)),
            )
        else:
            link = _make_link(chrono, kind)
            link.Initialize(child, parent, frame)

        _call(link, ("SetName", "SetNameString"), name)
        _call(system, ("AddLink", "Add"), link)
        links[name] = link

    return system, bodies, links, direct


#: Said when the reaction could not be rotated into world coordinates. It is **not** the
#: general caveat it used to be: which of Chrono's two reactions Kryova wants, and which
#: frame it arrives in, are both measured now — see `_reaction`.
JOINT_FRAME_CAVEAT = (
    "This reaction is stated in the joint's own frame rather than in world coordinates: "
    "this PyChrono build offered no way to rotate it."
)


def _reaction(link: Any) -> tuple[list[float], list[float], list[float], str]:
    """One joint's (location mm, force N, moment N·mm, caveat), in world coordinates.

    **`GetReaction1`, not `GetReaction2`, and that is measured rather than reasoned.**
    `_build` calls `Initialize(child, parent, frame)`, so body 1 is the child. On a 2 kg
    mass spun at 10 rad/s on a 100 mm crank (2026-09-16, `kryova-chrono:9.0.1`), both
    reactions have magnitude 20.0008 N — the closed-form `m w^2 r` — and they point
    opposite ways: `GetReaction1` is **inward**, the centripetal force holding the mass
    on its circle, and `GetReaction2` is outward, the load the mass puts on the ground.
    Kryova's `JointReaction` is *the load the parent applies to the child*, which is the
    first. Taking the second would have published every joint load with the sign
    reversed, at exactly the right magnitude — the most believable kind of wrong number
    there is.

    **The wrench is bound to a name before `.force` is read, and that is load-bearing.**
    `link.GetReaction2().force` chained in one expression returns a dangling reference on
    this build: the wrench is a temporary, it is freed the moment the expression ends, and
    the vector read out of it is garbage. Measured the same day, it printed
    `-4.86e188` — not a small error, and not one that looks like a memory bug either,
    because a plausible value would have been indistinguishable from a real answer.

    **The reaction arrives in the joint frame.** A horizontal rod at rest under gravity
    along -z reports `(0, -4e-5, 0)` and rotates to `(0, 0, -4e-5)`; the world answer must
    be along z, so the rotation is necessary and not a precaution.
    """
    caveat = ""

    wrench = _maybe(link, ("GetReaction1",))
    force = getattr(wrench, "force", None) if wrench is not None else None
    torque = getattr(wrench, "torque", None) if wrench is not None else None
    if force is None:
        force = _maybe(link, ("Get_react_force",))
        torque = _maybe(link, ("Get_react_torque",))
        if force is not None:
            caveat = (
                "This build has no GetReaction1, so the reaction came from the Chrono 8 "
                "accessors, whose sign convention has not been checked here."
            )
    if force is None:
        return (
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            "This PyChrono build reported no reaction at all, so it reads as zero here. "
            "A joint carrying nothing and a joint whose load is unknown are opposites.",
        )

    # **Frame 1, to match reaction 1.** `GetReaction1` is expressed in the link's *first*
    # frame, which is the child's and therefore turns with it. Rotating it by
    # `GetFrame2Abs` — the parent's, which on a joint to ground never moves — leaves a
    # vector of the right magnitude pointing the wrong way, and it was written that way
    # first: the spun mass then reported a constant (-19.9998, 0.2, 0) at every sample
    # while the exact evaluator had it sweeping round the circle. The magnitudes agreed
    # to 0.004% throughout, so a check on |F| alone would have passed.
    frame = _maybe(link, ("GetFrame1Abs", "GetLinkAbsoluteCoords"))
    location = [0.0, 0.0, 0.0]
    position = _maybe(frame, ("GetPos",))
    if position is not None:
        location = [_m_to_mm(v) for v in _xyz(position)]

    world_force = _maybe(frame, ("TransformDirectionLocalToParent",), force)
    world_torque = (
        _maybe(frame, ("TransformDirectionLocalToParent",), torque)
        if torque is not None
        else None
    )
    if world_force is None:
        caveat = (caveat + " " + JOINT_FRAME_CAVEAT).strip()
        world_force, world_torque = force, torque

    moment = (
        [_m_to_mm(v) for v in _xyz(world_torque)]
        if world_torque is not None
        else [0.0, 0.0, 0.0]
    )
    return (location, _xyz(world_force), moment, caveat)


def simulate(chrono: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Build the system, step it, and collect motion and reactions at each sample."""
    system, bodies, links, direct = _build(chrono, spec)

    duration = float(spec["motion"]["duration_s"])
    samples = int(spec["motion"]["samples"])
    if samples < 2:
        raise RuntimeError("A motion range needs at least two samples.")
    interval = duration / (samples - 1)
    step = min(interval / STEPS_PER_SAMPLE, MAX_STEP_S)

    times: list[float] = []
    motion: dict[str, dict[str, list[Any]]] = {
        name: {
            "frame_origin_mm": [],
            "frame_rotation": [],
            "position_mm": [],
            "velocity_mm_s": [],
            "acceleration_mm_s2": [],
            "angular_velocity_rad_s": [],
            "angular_acceleration_rad_s2": [],
        }
        for name in bodies
    }
    reactions: dict[str, dict[str, Any]] = {
        name: {"location_mm": [], "force_n": [], "moment_n_mm": [], "moment_caveat": ""}
        for name in links
    }

    rotation_unavailable = False

    for index in range(samples):
        target = index * interval
        # Stepped *to* the sample time rather than *by* the sample interval: the
        # integrator's step is chosen above and is not the caller's plotting resolution.
        while system.GetChTime() < target - 1e-12:
            system.DoStepDynamics(min(step, target - system.GetChTime()))
        times.append(float(system.GetChTime()))

        for name, body in bodies.items():
            record = motion[name]
            # A plain `ChBody`'s frame origin *is* its centre of mass (that separation is
            # what `ChBodyAuxRef` is for), so the pose origin and the centre-of-mass
            # position are the same point here and are written from one reading.
            position = [_m_to_mm(v) for v in _xyz(body.GetPos())]
            record["position_mm"].append(position)
            record["frame_origin_mm"].append(position)
            rows = _rotation_rows(_maybe(body, ("GetRot",)))
            rotation_unavailable = rotation_unavailable or not rows
            record["frame_rotation"].append(rows)
            record["velocity_mm_s"].append(
                [_m_to_mm(v) for v in _xyz(_call(body, ("GetPosDt", "GetPos_dt")))]
            )
            record["acceleration_mm_s2"].append(
                [_m_to_mm(v) for v in _xyz(_call(body, ("GetPosDt2", "GetPos_dtdt")))]
            )
            record["angular_velocity_rad_s"].append(
                _xyz(_call(body, ("GetAngVelLocal", "GetWvel_loc")))
            )
            record["angular_acceleration_rad_s2"].append(
                _xyz(_call(body, ("GetAngAccLocal", "GetWacc_loc")))
            )

        for name, link in links.items():
            location, force, moment, caveat = _reaction(link)
            reactions[name]["location_mm"].append(location)
            reactions[name]["force_n"].append(force)
            reactions[name]["moment_n_mm"].append(moment)
            reactions[name]["moment_caveat"] = caveat

    warnings: list[str] = []
    assumed = sorted(
        str(b["name"]) for b in spec.get("bodies", []) if not b.get("has_inertia_tensor")
    )
    if assumed:
        shown = ", ".join(assumed[:6]) + (", …" if len(assumed) > 6 else "")
        warnings.append(
            f"{len(assumed)} of this mechanism's bodies ({shown}) were given a "
            f"negligible isotropic inertia of {NEGLIGIBLE_INERTIA_KG_M2:g} kg m^2 "
            "because no tensor was sent. A zero tensor makes the mass matrix singular, "
            "so this keeps the solve conditioned; it also means no moment that depends "
            "on rotational inertia is represented."
        )
    if rotation_unavailable:
        warnings.append(
            "This PyChrono build offered no readable body rotation, so every pose "
            "carries its position and the identity rotation. A clearance sweep placing "
            "geometry by these frames would not see a body turn."
        )
    if not direct:
        # Loud, because the numbers stay plausible. See `SOLVER_TYPE`: the same pendulum
        # reads 29.42 N with a direct solver and 4286 N without, and nothing in the
        # result itself would tell them apart.
        warnings.append(
            f"This PyChrono build has no {SOLVER_TYPE} solver, so Chrono's default "
            "iterative solver answered. Measured on 2026-09-16, that solver does not "
            "satisfy a revolute constraint on this class of model: a pendulum whose "
            "closed-form peak pivot reaction is 29.42 N reported 4286 N, with the rod "
            "stretching from 0.5 m to 0.74 m. Treat every number here as unusable until "
            "the image is rebuilt with a direct solver."
        )
    if links and any(
        not any(reactions[name]["force_n"][0]) for name in reactions
    ):
        # Chrono has no constraint force until it has taken a step, so the first sample
        # reads zero. That is "not computed yet", not "carries nothing", and the two are
        # opposites — measured 2026-09-16, where the exact evaluator gives the full
        # 20 N at t=0 and Chrono gives (0, 0, 0).
        warnings.append(
            "The reaction at the first sample (t = 0) is zero because Chrono forms no "
            "constraint force until it has taken a step, not because the joint carries "
            "nothing. Read the first sample as unmeasured; every later one is a "
            "computed load."
        )
    seen = {reactions[name]["moment_caveat"] for name in reactions}
    warnings.extend(sorted(note for note in seen if note))

    return {
        "wire_version": WIRE_VERSION,
        "times_s": times,
        "bodies": motion,
        "reactions": reactions,
        "warnings": warnings,
        "method": (
            "Project Chrono time integration (ChSystemNSC, "
            f"{SOLVER_TYPE if direct else 'default iterative'} solver, fixed step "
            f"{step:g} s, {STEPS_PER_SAMPLE} per reported sample)"
        ),
        "chrono_version": str(getattr(chrono, "__version__", "unknown")),
    }


def main() -> int:
    work = Path(__file__).resolve().parent
    try:
        spec = json.loads((work / "input.json").read_text(encoding="utf-8"))
        if spec.get("wire_version") != WIRE_VERSION:
            raise RuntimeError(
                f"input.json speaks wire version {spec.get('wire_version')!r}; this "
                f"entry point speaks {WIRE_VERSION}."
            )
        import pychrono  # noqa: PLC0415 -- only exists inside the container

        if not hasattr(pychrono, "ChSystemNSC"):
            raise RuntimeError(
                "The importable 'pychrono' is not Project Chrono: it has no ChSystemNSC. "
                "The PyPI package of that name is an unrelated timing utility."
            )
        result = simulate(pychrono, spec)
    except Exception as exc:  # noqa: BLE001 -- the boundary; everything becomes a message
        (work / "output.json").write_text(
            json.dumps(
                {
                    "wire_version": WIRE_VERSION,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return 1

    (work / "output.json").write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
