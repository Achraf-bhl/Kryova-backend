"""What every joint carries -- master plan 9.4, the step that produces a defensible load.

Given exact accelerations from `kinematics.py`, the reaction at a joint of a **serial
(tree) chain** is not a solver problem. It is Newton's second law applied to a free body,
and there is exactly one free body per joint because a tree has exactly one path between
any two links. Cut the chain at the joint, sum over everything distal:

    F = sum_j  m_j * (a_j - g)
    M = sum_j  [ (c_j - p) x m_j*(a_j - g) + I_j*alpha_j + omega_j x (I_j*omega_j) ]

with `p` the joint's location, `c_j` each body's centre of mass, and both sums over the
bodies the joint carries. That is exact, closed form, and needs no engine -- which is
why this phase has a real deliverable on a machine where PyChrono could not be installed.

**It is exact only because the chain is a tree, and that is checked, not assumed.** A
closed loop distributes its reactions between the branches according to the stiffnesses,
which is genuinely indeterminate for rigid bodies and needs a constrained solve. There
the reaction is reported UNAVAILABLE with that reason, never as a number. This is
Decision 3 applied where it costs something: it would be very easy to sum the loop one
way round and quote the answer.

**The sign convention is stated once and obeyed everywhere**: the reported force and
moment are those applied *by the parent side onto the child side*, at the joint. That is
the load the bracket carrying the pin feels, so it drops into a `LoadCase` with no sign
to remember -- and getting it backwards produces a part sized for a load pointing the
wrong way, which passes every test that does not check direction.

**Gravity is in the reaction, and it must be.** `a - g` rather than `a` is the whole
d'Alembert content: a stationary link still hangs off its pin. A recursion that used `a`
alone would report zero for a parked machine, which is not what a bearing feels.

**A body with no inertia tensor keeps an exact force and an approximated moment.** `F =
m*a` does not involve inertia at all, so the force is unaffected; the moment loses
`I*alpha + omega x I*omega`, which for a fast-spinning link is not small. The reaction
carries a `moment_caveat` naming the bodies, and `MechanismResult.to_measurement_payload`
renders that as APPROXIMATED provenance on the moment path and MEASURED on the force
path beside it -- per path, exactly as `app.kernel.provenance` intends.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.dynamics.errors import MechanismError
from app.dynamics.kinematics import descendants, topological_order
from app.dynamics.pose import Rot3, Vec3, add, apply, cross, scale, sub, transpose
from app.dynamics.types import (
    MASS_KG_TO_TONNE,
    JointReaction,
    Mechanism,
    MotionPath,
)

#: Reported as the moment's caveat when a body was treated as a point mass.
POINT_MASS_CAVEAT = (
    "point-mass idealisation: no inertia tensor was given for {bodies}, so the moment "
    "omits the I*alpha and omega x I*omega terms. The force beside it is unaffected -- "
    "F = m*a does not involve inertia. Give Body.inertia_kg_mm2 to make the moment exact."
)

#: Reported as the reason when the mechanism is not a tree.
LOOP_REASON = (
    "this mechanism contains a closed kinematic loop, which distributes its reactions "
    "between the branches according to their stiffness. For rigid bodies that is "
    "statically indeterminate and no free-body sum can resolve it -- it needs a "
    "constrained dynamics solve. Run it on a dynamics engine, or open the loop and "
    "prescribe the released coordinate."
)


def _euler_moment(
    inertia_kg_mm2: Vec3,
    rotation: Rot3,
    omega: Vec3,
    alpha: Vec3,
) -> Vec3:
    """The rotational part of the moment, in N.mm.

    The tensor is diagonal in the *body* frame, so it is rotated into world as
    `R * diag(I) * R^T` before it multiplies a world-frame `omega` or `alpha`. Skipping
    that rotation is the classic error: it makes a slender link behave as though it were
    always aligned with the global axes, which is right at t = 0 and wrong everywhere
    after.

    Inertia arrives in kg.mm2 and the result must be N.mm = t.mm2/s2, so it carries the
    same single `MASS_KG_TO_TONNE` factor the force does. One conversion, named once.
    """
    rt = transpose(rotation)

    def times_inertia(v: Vec3) -> Vec3:
        local = apply(rt, v)
        scaled = (
            local[0] * inertia_kg_mm2[0],
            local[1] * inertia_kg_mm2[1],
            local[2] * inertia_kg_mm2[2],
        )
        return apply(rotation, scaled)

    return scale(
        add(times_inertia(alpha), cross(omega, times_inertia(omega))),
        MASS_KG_TO_TONNE,
    )


def compute(mechanism: Mechanism, path: MotionPath) -> Mapping[str, JointReaction]:
    """Every joint's reaction through the range, or a named reason it has none.

    Never raises for a mechanism it cannot handle -- it returns reactions carrying
    `unavailable_reason`, because a caller asking for the loads on a five-joint machine
    should get the four it can have plus a reason for the fifth, not an exception that
    loses all five. It *does* raise `MechanismError` when the path and the mechanism do
    not describe the same thing, which is a programming mistake rather than a modelling
    limit.
    """
    try:
        joints = topological_order(mechanism)
    except MechanismError as exc:
        return {
            joint.name: JointReaction(joint=joint.name, unavailable_reason=f"{LOOP_REASON} ({exc})")
            for joint in mechanism.joints
        }

    missing = [body.name for body in mechanism.bodies if body.name not in path.bodies]
    if missing:
        raise MechanismError(
            f"The motion path has no entry for {', '.join(sorted(missing))}, so the "
            f"reactions of {mechanism.name!r} cannot be summed over its own bodies. The "
            "path was computed for a different mechanism -- re-run kinematics.evaluate "
            "on this one."
        )

    gravity = mechanism.gravity_mm_s2
    times = path.times_s
    out: dict[str, JointReaction] = {}

    for joint in joints:
        carried = descendants(mechanism, joint.name)
        point_masses = sorted(
            name for name in carried if mechanism.body(name).is_point_mass
        )
        joint_path = path.joint_paths.get(joint.name)
        if joint_path is None:
            raise MechanismError(
                f"The motion path records no location for joint {joint.name!r}. Compute "
                "it with kinematics.evaluate on this mechanism rather than assembling "
                "the path by hand."
            )

        forces: list[Vec3] = []
        moments: list[Vec3] = []
        for index in range(len(times)):
            total_force: Vec3 = (0.0, 0.0, 0.0)
            total_moment: Vec3 = (0.0, 0.0, 0.0)
            pivot = joint_path[index]
            for name in carried:
                body = mechanism.body(name)
                motion = path.motion(name)
                accel = motion.acceleration_mm_s2[index]
                # d'Alembert: the force the joint must supply is m*(a - g).
                inertial = scale(
                    sub(accel, gravity), body.mass_kg * MASS_KG_TO_TONNE
                )
                total_force = add(total_force, inertial)
                lever = sub(motion.position_mm[index], pivot)
                total_moment = add(total_moment, cross(lever, inertial))
                if body.inertia_kg_mm2 is not None:
                    total_moment = add(
                        total_moment,
                        _euler_moment(
                            body.inertia_kg_mm2,
                            motion.frames[index].rotation,
                            motion.angular_velocity_rad_s[index],
                            motion.angular_acceleration_rad_s2[index],
                        ),
                    )
            forces.append(total_force)
            moments.append(total_moment)

        caveat = ""
        if point_masses:
            caveat = POINT_MASS_CAVEAT.format(bodies=", ".join(point_masses))

        out[joint.name] = JointReaction(
            joint=joint.name,
            times_s=times,
            location_mm=tuple(joint_path),
            force_n=tuple(forces),
            moment_n_mm=tuple(moments),
            moment_caveat=caveat,
        )

    return out


def free_body_check(
    mechanism: Mechanism, path: MotionPath, reactions: Mapping[str, JointReaction]
) -> float:
    """The worst force residual, in N, of summing the chain two different ways.

    A joint's reaction must equal the reactions of every joint hanging off it plus its
    own body's inertial force. Checking that costs nothing and catches a wrong
    descendant set, a mismatched sample index, or a sign flip in the recursion -- the
    same argument `_residual_is_small` makes in `app/solve/linear_static.py`: an answer
    that satisfies no conservation law is not an answer.

    Returns the largest absolute discrepancy over every joint and every instant, which
    is zero to rounding for a correct recursion. Callers assert on it; nothing here
    decides what "small" means, because that depends on the forces involved.
    """
    from app.dynamics.kinematics import joint_children

    children = joint_children(mechanism)
    worst = 0.0
    for joint in mechanism.joints:
        reaction = reactions.get(joint.name)
        if reaction is None or not reaction.available:
            continue
        body = mechanism.body(joint.body)
        motion = path.motion(joint.body)
        for index in range(len(path.times_s)):
            own = scale(
                sub(motion.acceleration_mm_s2[index], mechanism.gravity_mm_s2),
                body.mass_kg * MASS_KG_TO_TONNE,
            )
            child_reactions = [reactions.get(name) for name in children.get(joint.body, ())]
            if any(c is None or not c.available for c in child_reactions):
                # One branch is unavailable, so the sum is not a check of anything.
                continue
            summed = own
            for child in child_reactions:
                assert child is not None  # narrowed by the guard above
                summed = add(summed, child.force_n[index])
            residual = sub(reaction.force_n[index], summed)
            worst = max(worst, max(abs(c) for c in residual))
    return worst


__all__ = ["LOOP_REASON", "POINT_MASS_CAVEAT", "compute", "free_body_check"]
