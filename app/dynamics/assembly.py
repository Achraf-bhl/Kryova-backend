"""A mechanism derived from the assembly, never modelled twice -- master plan 9.2.

`types.Mechanism` is written by hand today: a body's mass, where its centre is, where the
pin sits in the parent's frame. Every one of those numbers already exists in the product
graph. The occurrence's frame says where the part is, the mass roll-up says what it weighs
and where its centre is, and the part's own drawing says where its bore is. Typing them in
a second time makes two copies that agree on the day they are typed and diverge on the day
somebody moves a bracket, with nothing erroring. This module reads them off the structure
instead.

**What is declared, and what is derived.** A joint is the one thing an assembly does not
say: a pin in a bore and a bolt through a clearance hole look identical in a product graph.
So a `JointDeclaration` names the child and parent occurrences, the kind, and where the
joint is **in the child part's own coordinates** (the bore centre, the slide axis, off the
part drawing). Everything else is derived:

* the joint's origin in the parent body's frame, from the two occurrences' world frames;
* the joint axis, rotated from the child part's frame into the world;
* each body's mass and centre of mass, from `app.assembly.mass.roll_up` over every leaf
  occurrence under the body's path, so a link that is a weldment of six plates weighs six
  plates.

**The frame convention, stated because the whole module rests on it.** `kinematics._advance`
gives a child body's frame the parent's rotation at q = 0, and ground is the world frame.
So at the assembled pose every body frame is parallel to the world, and a vector in a body
frame at rest is a world vector. The derivation is therefore done entirely in world
coordinates at the assembled pose: origin = joint position minus the parent joint's
position, centre of mass = centre minus the body's own joint position. **The assembled pose
is q = 0 for every joint**, and a driver's offset moves it from there.

**What it refuses, by name:**

* a body with any occurrence under it that could not be weighed, listing them. A mechanism
  built on a partial mass produces a reaction that is too small, which is the direction
  every check passes;
* two bodies that share mass, because one's path is inside the other's;
* a joint naming an occurrence that is not a declared body (or ground);
* a body with no joint, or with two, because a serial chain gives each body one parent.

**Inertia is derived when, and only when, you pass a measurer for it.** `measure` alone
answers at `Detail.FULL`, which carries a mass and a centre and **no tensor** — the tensor
is a fourth integration and `Detail.INERTIA` is a higher level that nothing computes
speculatively. So `derive` takes a second, optional measurer, `measure_inertia`, and:

* without it, every body is a point mass exactly as before, `reactions.py` marks the joint
  moments approximated and names the body, and the forces stay exact;
* with it, `app.assembly.inertia.roll_up_inertia` measures every component's tensor and
  assembles each body's own sub-tree by the parallel-axis theorem, about that body's
  centre of mass, in world axes at the assembled pose — which is the frame `Body` wants,
  because at q = 0 every body frame is parallel to the world (see above);
* a tensor the caller supplies in `inertia_kg_mm2` **wins over a measured one** for that
  body, because a caller who has measured a real part on a scale knows something this
  does not, and the note says which body came from where.

**A body whose products of inertia are large stays a point mass, and says so.** `Body`
carries only the diagonal, and an L-shaped link couples 18% of its largest moment into the
other two axes; `inertia.body_diagonal` refuses that rather than halving a bearing moment
silently, and `derive` records the refusal as a note instead of raising — one awkward link
must not cost the other eleven their inertia.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.assembly.inertia import InertiaError, body_diagonal, roll_up_inertia
from app.assembly.mass import ComponentMeasurer, MassRollup, roll_up
from app.assembly.structure import PATH_SEPARATOR, ProductStructure
from app.dynamics.errors import MechanismError
from app.dynamics.pose import Vec3, norm, sub
from app.dynamics.types import GRAVITY_DOWN_MM_S2, Body, Driver, Joint, JointKind, Mechanism


@dataclass(frozen=True)
class JointDeclaration:
    """The one fact about a joint the product graph does not hold.

    `child` and `parent` are occurrence paths. `parent=None` is ground. `at_mm` and
    `axis` are in the **child part's own coordinates**, the ones its drawing uses: the
    bore centre and the bore axis for a pin, a point on the slide and its direction for a
    prismatic joint. They are rotated into the world by the child occurrence's frame.
    """

    name: str
    kind: JointKind
    child: str
    parent: str | None = None
    at_mm: Vec3 = (0.0, 0.0, 0.0)
    axis: Vec3 = (0.0, 0.0, 1.0)


@dataclass(frozen=True)
class DerivedMechanism:
    """The mechanism, and where each of its numbers came from."""

    mechanism: Mechanism
    #: The roll-up every body's mass was taken from, complete for every body.
    rollup: MassRollup
    #: Body name to the occurrence paths whose mass it carries.
    occurrences: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: The joint's position in world coordinates at the assembled pose, by joint name.
    joint_positions_mm: Mapping[str, Vec3] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def _under(path: str, body_path: str) -> bool:
    return path == body_path or path.startswith(body_path + PATH_SEPARATOR)


def _body_name(path: str) -> str:
    """A body is named by its occurrence path, with the separator made legal."""
    return path.replace(PATH_SEPARATOR, "__").replace(".", "_")


def _measure_inertia(
    structure: ProductStructure,
    measure_inertia: ComponentMeasurer,
    body_paths: Sequence[str],
    masses: Mapping[str, tuple[float, Vec3]],
) -> tuple[dict[str, Vec3], list[str]]:
    """Each body's principal diagonal from its own geometry, and what could not be had.

    The tensor is assembled about the body's **own** centre of mass — the one the mass
    roll-up already computed for it, not the whole machine's — because that is the point
    `Body.inertia_kg_mm2` is about and shifting to the wrong one is a parallel-axis term
    of exactly the wrong size.

    Nothing here raises. A body with a missing tensor, or one whose products of inertia
    are too large for a diagonal to represent, is left out of the mapping and named in a
    note: it becomes a point mass, which is what it was before this function existed, and
    the note says which of the two reasons applied. Raising would let one awkward link
    cost the other eleven their inertia, which is the trade `mass.roll_up` already refuses
    to make for one pathological part.
    """
    rollup = roll_up_inertia(structure, measure_inertia)
    diagonals: dict[str, Vec3] = {}
    notes: list[str] = []
    for path in body_paths:
        own = rollup.under(path)
        if own.missing:
            listing = "; ".join(str(m) for m in own.missing[:3])
            more = "" if len(own.missing) <= 3 else f" and {len(own.missing) - 3} more"
            notes.append(
                f"{path} has no measured inertia: {len(own.missing)} occurrence(s) under "
                f"it carry no tensor ({listing}{more})."
            )
            continue
        if not own.weighed:
            notes.append(
                f"{path} has no measured inertia: no occurrence under it was measured."
            )
            continue
        tensor = own.about(masses[path][1])
        try:
            diagonals[path] = body_diagonal(tensor, body=path)
        except InertiaError as exc:
            notes.append(f"{path} stays a point mass. {exc}")
    return diagonals, notes


def derive(
    structure: ProductStructure,
    joints: Sequence[JointDeclaration],
    measure: ComponentMeasurer,
    *,
    drivers: Sequence[Driver] = (),
    name: str | None = None,
    gravity_mm_s2: Vec3 = GRAVITY_DOWN_MM_S2,
    inertia_kg_mm2: Mapping[str, Vec3] | None = None,
    measure_inertia: ComponentMeasurer | None = None,
) -> DerivedMechanism:
    """Build a `Mechanism` whose bodies are occurrences of `structure`.

    Each joint's `child` is a body. `drivers` name joints exactly as `joints` does.
    `inertia_kg_mm2` maps a child occurrence path to a principal diagonal about the
    centre of mass **in world axes at the assembled pose**; bodies not in it are point
    masses unless `measure_inertia` supplies one, and the module docstring says what a
    point mass costs.

    `measure_inertia` is a `ComponentMeasurer` answering at `Detail.INERTIA` —
    `app.assembly.inertia.from_document` builds one. It is a *separate* argument from
    `measure` rather than a flag on it because the two ask the kernel different questions
    at different cost, and a mass budget must not silently start paying for a fourth
    integration per component.
    """
    if not joints:
        raise MechanismError(
            "A mechanism needs at least one joint. Declare how the first moving part is "
            "held by ground with a JointDeclaration(parent=None)."
        )

    body_paths: list[str] = []
    for joint in joints:
        structure.occurrence(joint.child)  # raises, naming the path, if it is not there
        if joint.child in body_paths:
            raise MechanismError(
                f"{joint.child} is the child of more than one joint. In a serial chain each "
                "body hangs from exactly one parent; a closed loop is closures.py's, not this."
            )
        body_paths.append(joint.child)
    for joint in joints:
        if joint.parent is not None and joint.parent not in body_paths:
            raise MechanismError(
                f"Joint {joint.name!r} hangs {joint.child} from {joint.parent}, which is not "
                "the child of any joint. Declare how that part is held, or give parent=None "
                "if it is ground."
            )
    for a in body_paths:
        for b in body_paths:
            if a != b and _under(b, a):
                raise MechanismError(
                    f"{b} is inside {a}, and both are bodies, so its mass would be counted "
                    "twice. Make the sub-assembly one body, or declare only its parts."
                )

    rollup = roll_up(structure, measure)
    occurrences: dict[str, tuple[str, ...]] = {}
    masses: dict[str, tuple[float, Vec3]] = {}
    for path in body_paths:
        missing = [m for m in rollup.missing if _under(m.path, path)]
        if missing:
            raise MechanismError(
                f"The body {path} cannot be weighed, so no reaction on it would be right: "
                + "; ".join(str(m) for m in missing)
                + ". A reaction computed on a partial mass is too small, which is the "
                "direction every check passes."
            )
        weighed = [w for w in rollup.weighed if _under(w.path, path)]
        if not weighed:
            raise MechanismError(
                f"No part under {path} was weighed. A body with no leaf occurrence has no "
                "mass to move; check the path names a part or a sub-assembly of parts."
            )
        total = sum(w.mass_kg for w in weighed)
        if total > 0.0:
            centre = tuple(
                sum(w.mass_kg * w.centre_of_mass_mm[k] for w in weighed) / total
                for k in range(3)
            )
        else:
            centre = structure.occurrence(path).frame.origin_mm
        occurrences[path] = tuple(w.path for w in weighed)
        masses[path] = (total, (float(centre[0]), float(centre[1]), float(centre[2])))

    positions: dict[str, Vec3] = {}
    axes: dict[str, Vec3] = {}
    for joint in joints:
        frame = structure.occurrence(joint.child).frame
        positions[joint.child] = frame.point(joint.at_mm)
        axis = frame.direction(joint.axis)
        if joint.kind in ("revolute", "prismatic") and norm(axis) <= 0.0:
            raise MechanismError(
                f"Joint {joint.name!r} is {joint.kind} with a zero-length axis. Give the "
                "bore or slide direction in the child part's coordinates."
            )
        axes[joint.child] = axis

    inertia = dict(inertia_kg_mm2 or {})
    unknown = sorted(set(inertia) - set(body_paths))
    if unknown:
        raise MechanismError(
            f"An inertia was given for {', '.join(unknown)}, which is not a body. Key it by "
            "the child occurrence path of a joint."
        )
    notes: list[str] = []
    measured_inertia: dict[str, Vec3] = {}
    if measure_inertia is not None:
        measured_inertia, inertia_notes = _measure_inertia(
            structure, measure_inertia, body_paths, masses
        )
        notes.extend(inertia_notes)

    bodies: list[Body] = []
    built_joints: list[Joint] = []
    for joint in joints:
        path = joint.child
        mass, centre = masses[path]
        own = positions[path]
        # The caller's tensor wins: somebody who weighed the real part on a bench knows
        # something the geometry does not, and silently preferring the measured one would
        # discard it with nothing said.
        tensor = inertia.get(path, measured_inertia.get(path))
        bodies.append(
            Body(
                name=_body_name(path),
                mass_kg=mass,
                centre_of_mass_mm=sub(centre, own),
                inertia_kg_mm2=tensor,
            )
        )
        if path in inertia:
            notes.append(
                f"{path}'s inertia is the caller's, in world axes at the assembled pose; "
                "nothing here measured it."
            )
        elif path in measured_inertia:
            notes.append(
                f"{path}'s inertia was measured from its geometry: the parallel-axis sum "
                "over every leaf under it, about its centre of mass, in world axes at the "
                "assembled pose."
            )
        else:
            notes.append(
                f"{path} is a point mass: no inertia tensor reached it, so the joint "
                "moments above it omit the I*alpha and omega x I*omega terms. Forces are exact."
            )
        parent_position = positions[joint.parent] if joint.parent is not None else (0.0, 0.0, 0.0)
        built_joints.append(
            Joint(
                name=joint.name,
                kind=joint.kind,
                body=_body_name(path),
                parent=_body_name(joint.parent) if joint.parent is not None else None,
                origin_mm=sub(own, parent_position),
                axis=axes[path],
            )
        )

    mechanism = Mechanism(
        name=name or structure.root,
        bodies=tuple(bodies),
        joints=tuple(built_joints),
        drivers=tuple(drivers),
        gravity_mm_s2=gravity_mm_s2,
    )
    return DerivedMechanism(
        mechanism=mechanism,
        rollup=rollup,
        occurrences=occurrences,
        joint_positions_mm={j.name: positions[j.child] for j in joints},
        notes=tuple(notes),
    )


def body_name(path: str) -> str:
    """The body name `derive` gives the occurrence at `path`."""
    return _body_name(path)


__all__ = ["DerivedMechanism", "JointDeclaration", "body_name", "derive"]
