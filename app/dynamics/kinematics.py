"""Where every body is, and how fast, through the motion range -- with no engine at all.

This is the half of Phase 9 that is real today, and it is worth being clear about why it
is not a consolation prize. Master plan 9.3 -- swept volume, interference through motion,
travel and lock checks -- is entirely a question about *positions*, and gate G3's
clearance-through-travel check needs nothing more than this module and
`app.kernel.interrogation`. Joint reactions (9.4) need accelerations, and this produces
those too, exactly.

**The recursion is exact, not integrated.** A serial (tree) chain whose every joint
coordinate is prescribed by a driver has closed-form velocities and accelerations: the
standard forward recursion of Newton-Euler,

    omega_i   = omega_p + z_i * qdot
    alpha_i   = alpha_p + z_i * qddot + omega_p x (z_i * qdot)
    v_i       = v_p + omega_p x r
    a_i       = a_p + alpha_p x r + omega_p x (omega_p x r)   [+ Coriolis, prismatic]

with `r` the vector from the parent's joint origin to this one. There is no time
stepping, so there is no integration error, no step size and no drift -- the answer at
t = 10000 s is as exact as the answer at t = 0. That is what makes the closed-form tests
in `tests/test_dynamics_kinematics.py` assert to 1e-9 rather than to a tolerance
somebody negotiated.

**The Coriolis term is present on prismatic joints and absent on revolute ones**, and
that asymmetry is correct rather than an oversight: for a revolute joint the vector to
the child's origin is fixed in the parent's frame, so its derivative in the moving frame
is zero. For a prismatic joint the child slides *within* the parent, so the transport
theorem contributes 2 * omega_p x (that sliding velocity). Dropping it makes a
telescoping arm on a slewing base wrong in a way that only shows up when both move at
once -- which is exactly the case a robot arm is bought for.

**What this cannot do, it refuses by name.**

* A **closed loop** (a four-bar, a slider-crank) has fewer degrees of freedom than it
  has joints, so its coordinates are not independent and no forward recursion reaches
  them. `closures.py` solves the two canonical planar loops in closed form; anything
  else needs a constrained solver, and `topological_order` says so and names both.
* A **spherical joint** has three coordinates and one driver cannot prescribe them.
* A **contact, a stop, a spring, a friction law** -- anything where the motion is an
  *output* rather than an input -- is not a kinematics question at all. That is what an
  engine is for, and `engine.py` is where one arrives.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.dynamics.errors import MechanismError
from app.dynamics.pose import (
    Frame,
    Vec3,
    add,
    apply,
    compose,
    cross,
    from_axis_angle,
    scale,
    unit,
)
from app.dynamics.types import (
    BodyMotion,
    Driver,
    Joint,
    Mechanism,
    MotionPath,
    MotionRange,
)

#: What `MotionPath.method` records for a path built here. Read into the provenance
#: sidecar, so a reviewer sees how the numbers were obtained and not merely that they
#: exist.
EXACT_METHOD = (
    "closed-form serial-chain recursion: poses composed exactly, velocities and "
    "accelerations from the Newton-Euler forward recursion with analytically "
    "differentiated drivers. No time integration, so no step-size error."
)

TABULATED_METHOD = (
    "closed-form serial-chain recursion, but at least one joint is driven by a "
    "tabulated motion whose derivatives are finite differences of a linear "
    "interpolant -- the accelerations are a difference quotient, not a derivative."
)


def topological_order(mechanism: Mechanism) -> tuple[Joint, ...]:
    """The joints, parents before children, or a refusal that names the problem.

    Four ways a chain fails to be a tree, and each gets its own message because each has
    a different fix:

    * a body moved by two joints -- a closed loop, or a duplicated joint;
    * a body no joint moves -- it is floating and has no prescribed motion;
    * a cycle among the joints -- a loop that does not reach ground;
    * a joint whose parent is not itself moved by anything reachable from ground.
    """
    moved_by: dict[str, Joint] = {}
    for joint in mechanism.joints:
        existing = moved_by.get(joint.body)
        if existing is not None:
            raise MechanismError(
                f"Body {joint.body!r} is moved by both {existing.name!r} and "
                f"{joint.name!r}. That is a closed kinematic loop, which this "
                "closed-form evaluator cannot pose -- its joint coordinates are not "
                "independent. Use app.dynamics.closures for a slider-crank or a "
                "four-bar, or run it on a constrained dynamics engine."
            )
        moved_by[joint.body] = joint

    grounded = {body.name for body in mechanism.bodies if body.name in moved_by}
    ordered: list[Joint] = []
    placed: set[str] = set()

    remaining = list(mechanism.joints)
    while remaining:
        progressed = False
        for joint in list(remaining):
            if joint.parent is None or joint.parent in placed:
                ordered.append(joint)
                placed.add(joint.body)
                remaining.remove(joint)
                progressed = True
        if not progressed:
            stuck = ", ".join(sorted(j.name for j in remaining))
            raise MechanismError(
                f"These joints never reach ground: {stuck}. Each one hangs off a body "
                "that nothing places, so the chain is a cycle or is floating. Give the "
                "chain a joint with parent=None."
            )

    unmoved = sorted({b.name for b in mechanism.bodies} - grounded)
    if unmoved:
        raise MechanismError(
            f"Nothing moves {', '.join(unmoved)}. A body with no joint has no "
            "prescribed motion, so its acceleration -- and every load derived from it "
            "-- is undefined. Attach it with a joint, using kind='fixed' if it is meant "
            "to be rigidly carried."
        )
    return tuple(ordered)


def _driver_state(
    mechanism: Mechanism, joint: Joint, t: float
) -> tuple[float, float, float, Driver | None]:
    """(q, qdot, qddot, driver) for one joint at one instant.

    A `fixed` joint has no coordinate and is held at zero. A revolute or prismatic joint
    with no driver is refused rather than held at zero: a mechanism with an
    unconstrained coordinate has no determined motion, and quietly pinning it would
    produce a plausible answer to a question nobody asked.
    """
    driver = mechanism.driver_for(joint.name)
    if joint.kind == "fixed":
        if driver is not None:
            raise MechanismError(
                f"Joint {joint.name!r} is fixed but has a driver. A fixed joint has no "
                "coordinate to prescribe -- make it revolute or prismatic, or drop the "
                "driver."
            )
        return (0.0, 0.0, 0.0, None)
    if joint.kind == "spherical":
        raise MechanismError(
            f"Joint {joint.name!r} is spherical: three rotational degrees of freedom, "
            "which one driver cannot prescribe and this evaluator does not integrate. "
            "Model it as three revolute joints on intersecting axes, or run the "
            "mechanism on a dynamics engine."
        )
    if driver is None:
        raise MechanismError(
            f"Joint {joint.name!r} is {joint.kind} and nothing drives it. Its motion is "
            "therefore an output of the forces on it, which is a dynamics problem, not "
            "a kinematic one. Add a Driver for it, or run it on a dynamics engine."
        )
    q, rate, accel = driver.at(t)
    return (q, rate, accel, driver)


class _State:
    """One frame's kinematic state at one instant, in world coordinates."""

    __slots__ = ("frame", "omega", "alpha", "velocity", "acceleration")

    def __init__(self) -> None:
        self.frame = Frame()
        self.omega: Vec3 = (0.0, 0.0, 0.0)
        self.alpha: Vec3 = (0.0, 0.0, 0.0)
        self.velocity: Vec3 = (0.0, 0.0, 0.0)
        self.acceleration: Vec3 = (0.0, 0.0, 0.0)


def _advance(parent: _State, joint: Joint, q: float, rate: float, accel: float) -> _State:
    """The child frame's state, given the parent's and the joint coordinate.

    The whole of the kinematics is here, in about twenty lines, and every one of them is
    a term in the transport theorem. Read it beside the module docstring.
    """
    child = _State()
    r_p = parent.frame.rotation

    if joint.kind == "revolute":
        axis_world = apply(r_p, unit(joint.axis))
        offset = apply(r_p, joint.origin_mm)
        child.frame = Frame(
            compose(r_p, from_axis_angle(joint.axis, q)),
            add(parent.frame.origin_mm, offset),
        )
        child.omega = add(parent.omega, scale(axis_world, rate))
        child.alpha = add(
            add(parent.alpha, scale(axis_world, accel)),
            cross(parent.omega, scale(axis_world, rate)),
        )
        child.velocity = add(parent.velocity, cross(parent.omega, offset))
        child.acceleration = add(
            add(parent.acceleration, cross(parent.alpha, offset)),
            cross(parent.omega, cross(parent.omega, offset)),
        )
        return child

    if joint.kind == "prismatic":
        slide_local = scale(unit(joint.axis), q)
        offset = apply(r_p, add(joint.origin_mm, slide_local))
        slide_velocity = apply(r_p, scale(unit(joint.axis), rate))
        slide_acceleration = apply(r_p, scale(unit(joint.axis), accel))
        child.frame = Frame(r_p, add(parent.frame.origin_mm, offset))
        child.omega = parent.omega
        child.alpha = parent.alpha
        child.velocity = add(
            add(parent.velocity, cross(parent.omega, offset)), slide_velocity
        )
        child.acceleration = add(
            add(
                add(parent.acceleration, cross(parent.alpha, offset)),
                cross(parent.omega, cross(parent.omega, offset)),
            ),
            add(scale(cross(parent.omega, slide_velocity), 2.0), slide_acceleration),
        )
        return child

    # fixed: carried rigidly, so it is a revolute joint frozen at zero.
    offset = apply(r_p, joint.origin_mm)
    child.frame = Frame(r_p, add(parent.frame.origin_mm, offset))
    child.omega = parent.omega
    child.alpha = parent.alpha
    child.velocity = add(parent.velocity, cross(parent.omega, offset))
    child.acceleration = add(
        add(parent.acceleration, cross(parent.alpha, offset)),
        cross(parent.omega, cross(parent.omega, offset)),
    )
    return child


def evaluate(
    mechanism: Mechanism,
    motion: MotionRange,
    *,
    allow_tabulated_acceleration: bool = False,
) -> MotionPath:
    """Sweep the mechanism through the range and report every body's motion.

    `allow_tabulated_acceleration` guards the one place this layer can produce a number
    that looks exact and is not. A tabulated driver's second derivative is a difference
    quotient of a piecewise-linear interpolant: zero inside every span and a step at
    every knot. Feeding that to `reactions.py` produces a joint force that is zero
    almost everywhere and enormous at the knots -- a load case nobody would sign. So it
    is refused unless asked for, and when it is asked for the path carries a warning and
    the provenance record says the accelerations are difference quotients.
    """
    joints = topological_order(mechanism)
    times = motion.times()

    tabulated = [
        driver.joint for driver in mechanism.drivers if not driver.derivatives_are_exact
    ]
    warnings: list[str] = []
    if tabulated:
        if not allow_tabulated_acceleration:
            raise MechanismError(
                f"Joint(s) {', '.join(sorted(tabulated))} are driven by a tabulated "
                "motion, whose accelerations are finite differences of a linear "
                "interpolant -- zero inside each span and a step at each knot. That is "
                "not an acceleration a load case should be built from. Fit a smooth "
                "driver, or pass allow_tabulated_acceleration=True to accept difference "
                "quotients and the caveat recorded with them."
            )
        warnings.append(
            f"accelerations at {', '.join(sorted(tabulated))} are finite differences of "
            "a piecewise-linear table, not derivatives of a smooth motion"
        )

    frames: dict[str, list[Frame]] = {body.name: [] for body in mechanism.bodies}
    positions: dict[str, list[Vec3]] = {body.name: [] for body in mechanism.bodies}
    velocities: dict[str, list[Vec3]] = {body.name: [] for body in mechanism.bodies}
    accelerations: dict[str, list[Vec3]] = {body.name: [] for body in mechanism.bodies}
    omegas: dict[str, list[Vec3]] = {body.name: [] for body in mechanism.bodies}
    alphas: dict[str, list[Vec3]] = {body.name: [] for body in mechanism.bodies}
    joint_paths: dict[str, list[Vec3]] = {joint.name: [] for joint in joints}

    for t in times:
        states: dict[str, _State] = {}
        ground = _State()
        for joint in joints:
            parent = ground if joint.parent is None else states[joint.parent]
            q, rate, accel, _ = _driver_state(mechanism, joint, t)
            child = _advance(parent, joint, q, rate, accel)
            states[joint.body] = child
            joint_paths[joint.name].append(child.frame.origin_mm)

        for body in mechanism.bodies:
            state = states[body.name]
            lever = apply(state.frame.rotation, body.centre_of_mass_mm)
            frames[body.name].append(state.frame)
            positions[body.name].append(add(state.frame.origin_mm, lever))
            velocities[body.name].append(add(state.velocity, cross(state.omega, lever)))
            accelerations[body.name].append(
                add(
                    add(state.acceleration, cross(state.alpha, lever)),
                    cross(state.omega, cross(state.omega, lever)),
                )
            )
            omegas[body.name].append(state.omega)
            alphas[body.name].append(state.alpha)

    motions: dict[str, BodyMotion] = {
        name: BodyMotion(
            body=name,
            times_s=times,
            frames=tuple(frames[name]),
            position_mm=tuple(positions[name]),
            velocity_mm_s=tuple(velocities[name]),
            acceleration_mm_s2=tuple(accelerations[name]),
            angular_velocity_rad_s=tuple(omegas[name]),
            angular_acceleration_rad_s2=tuple(alphas[name]),
        )
        for name in frames
    }

    return MotionPath(
        mechanism=mechanism.name,
        times_s=times,
        bodies=motions,
        joint_paths={name: tuple(path) for name, path in joint_paths.items()},
        method=TABULATED_METHOD if tabulated else EXACT_METHOD,
        warnings=tuple(warnings),
    )


def descendants(mechanism: Mechanism, joint_name: str) -> tuple[str, ...]:
    """Every body carried by `joint_name`, itself included -- its free body.

    What the backward reaction recursion sums over. Kept here rather than in
    `reactions.py` because it is a fact about the topology, and the topology is this
    module's subject.
    """
    joints = topological_order(mechanism)
    by_parent: dict[str | None, list[Joint]] = {}
    for joint in joints:
        by_parent.setdefault(joint.parent, []).append(joint)

    root = mechanism.joint(joint_name)
    collected: list[str] = []
    queue: list[str] = [root.body]
    while queue:
        current = queue.pop()
        collected.append(current)
        queue.extend(child.body for child in by_parent.get(current, ()))
    return tuple(collected)


def joint_children(mechanism: Mechanism) -> Mapping[str, tuple[str, ...]]:
    """Body name -> the joints hanging off it. A convenience for callers walking a tree."""
    out: dict[str, list[str]] = {}
    for joint in mechanism.joints:
        if joint.parent is not None:
            out.setdefault(joint.parent, []).append(joint.name)
    return {name: tuple(names) for name, names in out.items()}


__all__ = [
    "EXACT_METHOD",
    "TABULATED_METHOD",
    "descendants",
    "evaluate",
    "joint_children",
    "topological_order",
]
