"""Turn a `LoadCaseSketch` -- words and numbers -- into the solver's `LoadCase`.

The split this module exists for: the model chooses *which* face and *how
much*, in a small vocabulary and a material slug, and Python builds the
selectors, the material record and the load objects. Nothing the model can say
produces an invalid shape, so the pydantic failures that cost ladder prompt H4
three rounds and five and a half minutes (2026-09-06) have nowhere to occur.

`realise` is deterministic and total over a valid sketch except for the cases
it refuses in words: a load with neither a force nor a pressure has no
magnitude, a load case whose every load is like that has nothing to solve, and
a part-relative face word with no bounding box to resolve it against.

## The geometry is an argument here, and gate G1 is why

This module used to take the sketch alone. The bounding box reached the
*prompt* and stopped there, which had two consequences that only showed up on
a real part (2026-09-08, `docs/verification-2026-09-08-G1/`):

1. **The vocabulary could not say "the far end".** Six absolute direction words
   cannot name the end of a beam that does not lie along X, and the tool's own
   description promised they could. See `schemas.FaceName`.
2. **Nothing could check a drafted case against the part it was drafted for.**
   A clamp and a load on faces 20 mm apart on a 200 mm part, with the force
   along the axis the part is long in, is recognisable nonsense and passed
   unremarked, out to a reported factor of safety of 1303.

Both need the same thing — the box — so it is passed in, and both are answered
here rather than in a prompt the engineer never sees.

## Why the sense checks warn rather than refuse

They land in `LoadCaseDraft.unresolved`, which the caller is already required
to show. A refusal would be the wrong instrument: a short span between a
support and a load is *legitimate* — a bolted flange in compression, a boss
pushed against its own seat — and this codebase's rule about over-refusal is
that the agent's recovery from one is to try something else, so an over-refusal
becomes a wrongly built part rather than a caught one. What was missing at G1
was not permission to run; it was anybody saying the number looked wrong. So
each check states the measurement that triggered it and names the word that
would fix it.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from app.ai.schemas import AppliedLoad, FaceName, LoadCaseDraft, LoadCaseSketch
from app.solve.materials import MATERIALS
from app.solve.types import (
    FaceSelector,
    Fixture,
    ForceLoad,
    GravityLoad,
    Load,
    LoadCase,
    PressureLoad,
)

Axis = Literal["x", "y", "z"]
Side = Literal["min", "max"]

_AXES: Final[tuple[Axis, ...]] = ("x", "y", "z")

#: The one convention that turns an absolute word into a selector. +Z up, +X
#: right, +Y away from the viewer -- the same frame the parsing prompt states,
#: so "the top" the model was told about and "the top" it is given here agree.
FACE_SELECTORS: Final[dict[str, tuple[Axis, Side]]] = {
    "top": ("z", "max"),
    "bottom": ("z", "min"),
    "right": ("x", "max"),
    "left": ("x", "min"),
    "back": ("y", "max"),
    "front": ("y", "min"),
}

#: The part-relative words, as the side of the long axis each one means. The
#: axis itself is measured from the bounding box, so these two are the only
#: entries whose meaning changes with the part.
RELATIVE_FACES: Final[dict[str, Side]] = {
    "far end": "max",
    "near end": "min",
}

#: A support and a load this much of the part's longest extent apart, or less,
#: are reported as a probable mistake. A tenth is deliberately far below the
#: G1 case (20 mm on 200 mm, exactly a tenth) rather than tuned to catch it:
#: the check must fire on that case and on anything worse, and must not fire on
#: a stubby part whose real span happens to be short. A part loaded across its
#: own thickness is the thing being caught, and thickness is rarely a tenth of
#: length by accident.
SUSPICIOUS_SPAN_FRACTION: Final = 0.10

#: Below this the bounding box is not a shape, and every ratio computed from it
#: is noise. In mm, and far under any real part.
_DEGENERATE_EXTENT_MM: Final = 1e-9


class SketchProblem(ValueError):
    """A sketch that cannot become a load case, said in words the model can act on."""


def _extents(bounding_box: dict[str, Any] | None) -> list[float] | None:
    """The box's size per axis, or None when there is no usable box.

    Tolerant of the shape on purpose: `size` is what `app.geometry.inspect`
    writes, but a box that carries only `min` and `max` still describes a part
    and refusing it would be a refusal about bookkeeping rather than geometry.
    """
    if not bounding_box:
        return None
    size = bounding_box.get("size")
    if size is None:
        low, high = bounding_box.get("min"), bounding_box.get("max")
        if low is None or high is None:
            return None
        try:
            size = [float(high[i]) - float(low[i]) for i in range(3)]
        except (TypeError, IndexError, ValueError):
            return None
    try:
        extents = [abs(float(value)) for value in size]
    except (TypeError, ValueError):
        return None
    if len(extents) != 3 or max(extents) <= _DEGENERATE_EXTENT_MM:
        return None
    return extents


def long_axis(bounding_box: dict[str, Any] | None) -> Axis | None:
    """The axis the part is longest in, or None when the box cannot say.

    Ties go to the earlier axis, which is arbitrary and has to be: a cube has no
    long axis, and any answer is as good as any other. What must not happen is a
    *silent* answer on a part that genuinely has one, which is why the caller
    refuses rather than defaults when this returns None.
    """
    extents = _extents(bounding_box)
    if extents is None:
        return None
    return _AXES[extents.index(max(extents))]


def resolve_face(face: FaceName, bounding_box: dict[str, Any] | None) -> tuple[Axis, Side]:
    """The axis and side one face word means for this part.

    Absolute words ignore the box. `far end` and `near end` need it and are
    refused by name without one — never resolved to a default axis, because a
    default axis is the defect these words were added to remove.
    """
    if face in FACE_SELECTORS:
        return FACE_SELECTORS[face]
    side = RELATIVE_FACES.get(face)
    if side is None:  # pragma: no cover - FaceName is a closed Literal
        raise SketchProblem(
            f"{face!r} is not a face this can name. Use one of "
            f"{', '.join(sorted(FACE_SELECTORS))}, or 'far end' / 'near end'."
        )
    axis = long_axis(bounding_box)
    if axis is None:
        raise SketchProblem(
            f"{face!r} means the end of the part's longest axis, and no bounding box "
            "was supplied to work out which axis that is. Name the face directly "
            f"({', '.join(sorted(FACE_SELECTORS))}), or export the geometry so the "
            "box is known."
        )
    return axis, side


def face_selector(face: FaceName, bounding_box: dict[str, Any] | None = None) -> FaceSelector:
    axis, side = resolve_face(face, bounding_box)
    return FaceSelector(axis=axis, side=side)


def _fixture(face: FaceName, kind: str, bounding_box: dict[str, Any] | None) -> Fixture:
    axis, _ = resolve_face(face, bounding_box)
    if kind == "clamp":
        return Fixture(
            where=face_selector(face, bounding_box),
            kind="clamp",
            name=f"{face} face held",
        )
    # A roller or a symmetry plane is normal to the face it sits on -- that is
    # the only reading of "a roller on the bottom" that holds the part up.
    return Fixture(
        where=face_selector(face, bounding_box),
        kind="roller" if kind == "roller" else "symmetry",
        normal=axis,
        name=f"{kind} on the {face} face",
    )


def _load(applied: AppliedLoad, bounding_box: dict[str, Any] | None) -> Load | None:
    """The solver's load for one applied load, or None when it has no magnitude."""
    if len(applied.force_n) == 3 and any(component != 0.0 for component in applied.force_n):
        x, y, z = (float(component) for component in applied.force_n)
        return ForceLoad(
            where=face_selector(applied.face, bounding_box),
            force_n=(x, y, z),
            name=f"force on the {applied.face} face",
        )
    if applied.pressure_mpa != 0.0:
        return PressureLoad(
            where=face_selector(applied.face, bounding_box),
            pressure_mpa=float(applied.pressure_mpa),
            name=f"pressure on the {applied.face} face",
        )
    return None


def _span_warnings(
    sketch: LoadCaseSketch, bounding_box: dict[str, Any] | None
) -> list[str]:
    """What is wrong with where this case holds and pushes the part.

    Two things, both measured against the box rather than guessed:

    **The support and the load are on the same face.** Holding a face and
    pushing the same face is not a structure; whatever comes back is a report
    about the fixture. Certain enough to state flatly.

    **They are barely apart on a part that is long.** This is the G1 case. The
    ratio is the separation between the two faces over the part's longest
    extent, so it is scale-free and reads the same on a bracket and on a beam.
    """
    extents = _extents(bounding_box)
    if extents is None:
        return []
    longest = max(extents)
    warnings: list[str] = []

    held = {support.face for support in sketch.supports}
    loaded = {applied.face for applied in sketch.loads}

    for face in sorted(held & loaded):
        warnings.append(
            f"The {face} face is both held and loaded. A face that is clamped cannot "
            "move, so the result will describe the fixture rather than the part — "
            "hold one face and load a different one."
        )

    for support_face in sorted(held):
        for load_face in sorted(loaded - held):
            try:
                support_axis, support_side = resolve_face(support_face, bounding_box)
                load_axis, load_side = resolve_face(load_face, bounding_box)
            except SketchProblem:  # pragma: no cover - realise refuses first
                continue
            if support_axis != load_axis or support_side == load_side:
                continue
            span = extents[_AXES.index(support_axis)]
            if span > SUSPICIOUS_SPAN_FRACTION * longest:
                continue
            long = _AXES[extents.index(longest)]
            warnings.append(
                f"The {support_face} face is held and the {load_face} face is loaded, "
                f"and they are only {span:g} mm apart across the part's {support_axis} "
                f"direction — the part is {longest:g} mm long in {long}. If you meant "
                "to hold one end and load the other, say 'near end' and 'far end'; if "
                "you did mean to load it across its thickness, this run is measuring "
                "that and the stiffness will be very high."
            )
    return warnings


def _direction_warnings(
    sketch: LoadCaseSketch, bounding_box: dict[str, Any] | None
) -> list[str]:
    """A force pushing a *side* face along the part's own length, said once.

    **Not "the force lies in the face", which was the first version of this
    check and was wrong.** A transverse tip load on the end of a cantilever is
    in-plane on that end face, and it is also the commonest correct load case
    there is — flagging it puts a warning on the right answer, and a list that
    cries wolf is one nobody reads. That is the same reasoning as this
    codebase's rule about over-refusal, one level down.

    What is genuinely odd is the G1 shape: a force along the axis the part is
    *long* in, applied to a face that is not an end. Loading the side of a beam
    along its length is either a mislabelled face or a real axial load applied
    in the wrong place, and both are worth a sentence.
    """
    extents = _extents(bounding_box)
    if extents is None:
        return []
    longest = _AXES[extents.index(max(extents))]

    warnings: list[str] = []
    for applied in sketch.loads:
        if len(applied.force_n) != 3 or not any(c != 0.0 for c in applied.force_n):
            continue
        try:
            axis, _side = resolve_face(applied.face, bounding_box)
        except SketchProblem:  # pragma: no cover - realise refuses first
            continue
        if axis == longest:
            continue  # an end face; a transverse load on one is the normal case
        force = [abs(float(component)) for component in applied.force_n]
        if _AXES[force.index(max(force))] != longest:
            continue
        warnings.append(
            f"The force on the {applied.face} face acts mainly along {longest}, which is "
            f"the direction the part is longest in, but that face is normal to {axis} — "
            "so the load runs along the face rather than onto an end of the part. Check "
            "the face is the one you meant; 'far end' and 'near end' name the ends."
        )
    return warnings


def realise(
    sketch: LoadCaseSketch, bounding_box: dict[str, Any] | None = None
) -> LoadCaseDraft:
    """The solver's load case for a sketch, with the sketch's caveats carried over.

    A load the sketch named without a magnitude is reported in `unresolved`
    rather than dropped silently -- the engineer asked for it and must see
    that it is not in the run. If nothing with a magnitude remains the sketch
    is refused, because a load case with no load is not a draft, it is a
    question.

    `bounding_box` is optional so every existing caller keeps working, and its
    absence costs exactly what it should: the part-relative words are refused
    by name and the sense checks stay quiet rather than guessing. It is not
    optional in the product — `app.ai.tools` already refuses to draft without
    one.
    """
    unresolved = list(sketch.unresolved)
    loads: list[Load] = []
    for applied in sketch.loads:
        load = _load(applied, bounding_box)
        if load is None:
            unresolved.append(
                f"The load on the {applied.face} face has no magnitude -- give it a "
                "force in newtons or a pressure in MPa."
            )
            continue
        loads.append(load)
    if sketch.self_weight:
        loads.append(GravityLoad(name="self weight"))
    if not loads:
        raise SketchProblem(
            "No load in the description has a magnitude. Say how much force, in "
            "newtons, or what pressure, in MPa, and on which face."
        )

    fixtures = [_fixture(support.face, support.kind, bounding_box) for support in sketch.supports]
    material = MATERIALS[sketch.material]

    case = LoadCase(
        name=sketch.name.strip() or "Load case",
        material=material,
        fixtures=fixtures,
        loads=loads,
    )
    assumptions = list(sketch.assumptions)
    # The face convention is a choice the engineer did not make and must be
    # able to check, so it travels with every draft rather than living only
    # in a prompt they never see.
    assumptions.append(
        "Faces are named on the part's bounding box with +Z up, +X to the right "
        "and +Y away from the viewer."
    )
    axis = long_axis(bounding_box)
    if axis is not None and any(
        face in RELATIVE_FACES
        for face in [s.face for s in sketch.supports] + [load.face for load in sketch.loads]
    ):
        assumptions.append(
            f"'near end' and 'far end' were read as the {axis}-min and {axis}-max faces, "
            f"because {axis} is the part's longest direction."
        )

    unresolved.extend(_span_warnings(sketch, bounding_box))
    unresolved.extend(_direction_warnings(sketch, bounding_box))
    return LoadCaseDraft(load_case=case, assumptions=assumptions, unresolved=unresolved)
