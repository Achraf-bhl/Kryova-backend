"""Solid features built from a sketch: pad, pocket, shaft, groove.

These are what makes the kernel useful — the operations a real part is actually made of.
Each takes a profile and turns it into material (pad, shaft) or removes it (pocket,
groove).

**Adding and removing are the same geometry with opposite signs.** A pad and a pocket
both prism a profile; the pad fuses the result into the part and the pocket cuts it out.
Writing them as one function with a boolean at the end keeps them from drifting — a bug
fixed in the pad's limit handling would otherwise have to be found again in the pocket's.

**The first solid feature has nothing to fuse into**, and that is not an error: a part
starts empty and its first pad *is* the part. Every later one combines.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import (
    contribution_of,
    evolution_of,
    record_derived,
    record_primitive,
)
from app.kernel.occt.operations.context import (
    BuildContext,
    as_point,
    as_positive_length,
    build_or_raise,
    given_name,
)
from app.kernel.occt.sketching import Sketch
from app.kernel.occt.topology import edges, faces

PAD = "catia_pad"
POCKET = "catia_pocket"
SHAFT = "catia_shaft"
GROOVE = "catia_groove"
SOLID_COMBINE = "catia_solid_combine"

#: Limit modes the registry allows that this backend still cannot honour, with what each
#: is actually waiting on. `up_to_next`, `up_to_last` and `up_to_plane` were here until
#: Phase 2.5 and are now computed — see `_limited_distance`.
#:
#: The reasons for the first two used to blame Phase 2.1's face selection. That shipped,
#: and the reasons were wrong from that moment: the missing piece was never *naming* the
#: face to stop against but *stopping* against it. Recorded because a stale "blocked on X"
#: outlives X and then sends the next reader to rebuild something that already exists —
#: which is why `up_to_surface` no longer says it is waiting for constructed surfaces
#: either. Those arrived with `operations/surfaces.py`; what is still missing is trimming
#: the extrusion against one, which is a cut and not a distance.
_UNSUPPORTED_LIMITS: dict[str, str] = {
    "up_to_surface": (
        "a surface can be named and built now, but a pad stopping at one is trimmed "
        "against it rather than run to a distance, and this backend still extrudes to a "
        "distance. Extrude past the surface and cut with catia_boolean, or stop at a "
        "plane with limit='up_to_plane'"
    ),
}

#: Limits that are resolved against the geometry rather than given as a distance.
_COMPUTED_LIMITS: Final[frozenset[str]] = frozenset(
    {"up_to_next", "up_to_last", "up_to_plane"}
)

#: Extents closer together than this along the extrusion direction are one surface, not
#: two. Well under any real wall and well over the noise of a bounding-box computation.
LIMIT_TOLERANCE_MM: Final = 1e-6


def pad(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extrude a profile into material, to a length or to a limit."""
    limit = _limited_distance(context, arguments, PAD, adds_material=True)
    length = (
        limit
        if limit is not None
        else as_positive_length(arguments.get("length_mm"), argument="length_mm", tool=PAD)
    )
    return _prism_feature(context, arguments, PAD, length, adds_material=True)


def pocket(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extrude a profile and remove it from the part, to a depth or to a limit."""
    limit = _limited_distance(context, arguments, POCKET, adds_material=False)
    if limit is not None:
        depth = limit
    elif arguments.get("through_all"):
        depth = _through_all_depth(context)
    else:
        depth = as_positive_length(arguments.get("depth_mm"), argument="depth_mm", tool=POCKET)
    return _prism_feature(context, arguments, POCKET, depth, adds_material=False)


def shaft(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Revolve a profile into material."""
    return _revolve_feature(context, arguments, SHAFT, adds_material=True)


def groove(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Revolve a profile and remove it from the part."""
    return _revolve_feature(context, arguments, GROOVE, adds_material=False)


# -- shared construction -----------------------------------------------------


def _prism_feature(
    context: BuildContext,
    arguments: Mapping[str, Any],
    tool: str,
    distance: float,
    *,
    adds_material: bool,
) -> Mapping[str, Any]:
    document = context.require_document()
    sketch = _sketch_argument(context, arguments, tool)
    _reject_unsupported_limit(arguments, tool)

    face = sketch.face()
    normal = sketch.frame().Direction()
    reversed_ = bool(arguments.get("reversed"))
    symmetric = bool(arguments.get("symmetric"))

    if symmetric:
        # Half each way, so the *total* is the length the author asked for. Extruding
        # the full length in both directions would make a part twice as thick as the
        # number in the spec, which is the kind of error nobody notices until assembly.
        #
        # The profile is **moved** back half a length and then extruded once. Extruding
        # it backwards and then extruding *that* forwards is the natural way to write
        # this and does not work: the first extrusion returns a solid, and
        # `BRepPrimAPI_MakePrism` refuses a solid with `Standard_NoSuchObject: Solids are
        # not Processed` — a C++ exception out of the constructor, before any `IsDone()`
        # the wrapper could have checked.
        blank = _prism(_translated(face, normal, -distance / 2.0), normal, distance, tool)
    else:
        blank = _prism(face, normal, -distance if reversed_ else distance, tool)

    return combine_into_part(context, document, arguments, tool, blank, adds_material=adds_material)


def _revolve_feature(
    context: BuildContext,
    arguments: Mapping[str, Any],
    tool: str,
    *,
    adds_material: bool,
) -> Mapping[str, Any]:
    document = context.require_document()
    sketch = _sketch_argument(context, arguments, tool)

    angle = float(arguments.get("angle_deg") or 360.0)
    if not 0.0 < angle <= 360.0:
        raise GeometryError(
            f"{tool} needs a revolution angle between 0 and 360 degrees; got {angle}."
        )

    if arguments.get("axis"):
        raise OperationNotSupported(
            f"{tool} with an externally named axis",
            "Naming an axis built elsewhere in the design is not resolved here yet. "
            "Draw the axis in the profile's own sketch with catia_sketch_axis, which is "
            "where CATIA puts it and is honoured",
        )
    axis = _revolution_axis(sketch)

    maker = symbol("BRepPrimAPI_MakeRevol")(sketch.face(), axis, math.radians(angle))
    blank = build_or_raise(
        maker,
        tool=f"{tool} through {angle}°",
        detail="A profile that crosses its own revolution axis cannot be revolved — "
        "move it clear of the axis.",
    )
    return combine_into_part(context, document, arguments, tool, blank, adds_material=adds_material)


def _revolution_axis(sketch: Any) -> Any:
    """The line a shaft or groove turns about.

    The sketch's own vertical through its origin when the design drew no axis — CATIA's
    convention, and the right default for a single-profile part sketched about the
    origin. A drawn axis wins, because a profile placed away from the origin needs one
    and there is no way to infer it: the same profile revolved about two different lines
    is two different parts, both plausible.
    """
    frame = sketch.frame()
    if sketch.axis is None:
        return symbol("gp_Ax1")(frame.Location(), frame.YDirection())

    start, end = sketch.axis
    tail, head = sketch.to_world(start), sketch.to_world(end)
    return symbol("gp_Ax1")(
        symbol("gp_Pnt")(*tail),
        symbol("gp_Dir")(head[0] - tail[0], head[1] - tail[1], head[2] - tail[2]),
    )


def _translated(shape: Any, normal: Any, distance: float) -> Any:
    """The same shape, moved along a direction. Used to place a symmetric profile."""
    transform = symbol("gp_Trsf")()
    transform.SetTranslation(
        symbol("gp_Vec")(
            normal.X() * distance, normal.Y() * distance, normal.Z() * distance
        )
    )
    return symbol("BRepBuilderAPI_Transform")(shape, transform, True).Shape()


def _prism(face: Any, normal: Any, distance: float, tool: str) -> Any:
    vector = symbol("gp_Vec")(normal.X() * distance, normal.Y() * distance, normal.Z() * distance)
    maker = symbol("BRepPrimAPI_MakePrism")(face, vector)
    return build_or_raise(
        maker,
        tool=tool,
        detail="The profile could not be extruded; check that it is a single closed "
        "outline that does not cross itself.",
    )


def combine_into_part(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    blank: Any,
    *,
    adds_material: bool,
) -> Mapping[str, Any]:
    """Fuse or cut the new material into the part, recording the naming history."""
    feature = document.add_feature(given_name(arguments), tool)
    existing = document.shape

    if existing is None:
        if not adds_material:
            raise GeometryError(
                f"{tool} removes material, and this part has none yet. Build a pad or a "
                "shaft before cutting into it."
            )
        # The first solid feature *is* the part; there is nothing to fuse into, so
        # everything it has is its own contribution.
        document.set_result(feature, blank, contributed=(faces(blank), edges(blank)))
        record_primitive(feature.labels, blank)
        return context.result_for(feature)

    operation = "BRepAlgoAPI_Fuse" if adds_material else "BRepAlgoAPI_Cut"
    maker = symbol(operation)(existing, blank)
    result = build_or_raise(
        maker,
        tool=f"{tool} ({'fuse' if adds_material else 'cut'})",
        detail="The new material does not meet the existing part, or the cut would "
        "remove all of it.",
    )
    if not _encloses_solid(result):
        raise GeometryError(
            f"{tool} removed everything the part was made of, leaving no solid. Check "
            "its depth and position against the material that is actually there."
        )
    _refuse_a_feature_that_changed_nothing(existing, result, tool, adds_material)

    modified, generated = evolution_of(maker, existing)
    document.set_result(
        feature,
        result,
        contributed=contribution_of(maker, blank),
        evolved_by=maker,
    )
    record_derived(
        feature.labels, result=result, source=existing, modified=modified, generated=generated
    )
    return context.result_for(feature)


#: Below this, a change in volume is boolean noise rather than material. A
#: 12 mm hole is 113 mm^2 of area; nothing a caller means to build is smaller
#: than a hundredth of a cubic millimetre.
_NO_CHANGE_MM3 = 1e-2


def _refuse_a_feature_that_changed_nothing(
    existing: Any, result: Any, tool: str, adds_material: bool
) -> None:
    """A cut that removes nothing, or a boss that adds nothing, is a mistake.

    **Measured at gate G1 on 2026-09-06.** Asked for four 12 mm clearance holes
    on a 200 mm bolt circle in a 200x150 plate, the agent drew four circles at a
    100 mm radius and pocketed them. On a plate spanning x +/- 100 and y +/- 75,
    two of those circles sit centred on the left and right edges — half in the
    material, half in the air — and the other two, at y = +/- 100, are **entirely
    off the part**. They cut nothing. The call returned `ok`. The finished plate
    has two small notches in its edges and no clearance holes at all, and the
    volume confirms it to the last digit:

        200*150*11.24 - pi*30^2*11.24 - 2*(half a 12 mm circle, 5 mm deep)
          = 304854.16 mm^3, exactly what was measured.

    Nothing anywhere said the holes had missed. `BRepAlgoAPI_Cut` succeeds
    perfectly well when the tool and the target do not overlap: the answer is
    the target, unchanged, and `IsDone()` is true.

    This is the same family as the two guards above it — an operation that
    reports success while achieving nothing — and it is refused rather than
    noted, because unlike an unused sketch there is no reading under which a
    caller meant it. A feature that changes no material is not a feature.

    **What this does NOT catch, and it is the honest limit:** the case above is
    only caught when *every* profile misses. Two of those four circles did
    overlap the plate, so the pocket as a whole removed 565 mm^3 and this guard
    stays silent. Catching a partial miss needs a per-profile check against the
    material, which is a larger piece of work and is not pretended here.
    """
    from app.kernel.occt.metrology import volume_mm3

    before = volume_mm3(existing)
    after = volume_mm3(result)
    if abs(after - before) > _NO_CHANGE_MM3:
        return

    if adds_material:
        raise GeometryError(
            f"{tool} added no material: the part is exactly the volume it was "
            f"({before:.3f} mm3). The new shape is entirely inside the part already, "
            "so there is nothing for it to add. Check its position and size."
        )
    raise GeometryError(
        f"{tool} removed no material: the part is exactly the volume it was "
        f"({before:.3f} mm3). The profile does not overlap the part at all, so the "
        "cut fell in the air beside it — a boolean cut with no overlap succeeds and "
        "returns the part unchanged. Check where the profile actually is: a hole on "
        "a bolt circle bigger than the part lands off the edge."
    )


def _encloses_solid(shape: Any) -> bool:
    from app.kernel.occt.topology import has_solid

    return has_solid(shape)


def _sketch_argument(context: BuildContext, arguments: Mapping[str, Any], tool: str) -> Sketch:
    document = context.require_document()
    named = arguments.get("sketch")
    if not named:
        raise GeometryError(_no_sketch_message(context, arguments, tool))
    _refuse_a_second_identical_build(context, arguments, tool, str(named))
    resolved = document.sketch(str(named))
    # Recorded here, at the one place every feature resolves its sketch, so a
    # new operation cannot forget to.
    document.note_sketch_used(str(named))
    return resolved


#: Arguments that only *size* a feature. Two calls differing in nothing but one
#: of these are the same feature at two sizes, which is a parameter change.
#: Anything else differing — a direction, an offset, a limit — makes the second
#: call a genuinely different feature and it is allowed through.
_DIMENSION_ARGUMENTS = frozenset(
    {"length_mm", "depth_mm", "angle_deg", "radius_mm", "thickness_mm", "name"}
)


def _refuse_a_second_identical_build(
    context: BuildContext, arguments: Mapping[str, Any], tool: str, sketch: str
) -> None:
    """Building the same sketch the same way twice is a resize, not a feature.

    **Measured at gate G1 on 2026-09-06, twice, by two different routes.** Asked
    to correct a plate's thickness to hit 2.4 kg, the agent padded the same
    sketch at 10 mm, then at 11 mm, then at 11.5 mm. All three succeeded. The
    pads are coincident so they fuse and the *geometry* comes out right — the
    part really is 11.5 mm thick — but the document ends carrying `Pad.1`,
    `Pad.2` and `Pad.3` where one pad was meant, and the design has no parameter
    to change any more. On the previous attempt the same intent arrived through
    `catia_pad {"feature": "Pad.1"}` and was caught by the message above; this is
    the door it takes when it phrases the request legally.

    The rule is narrow on purpose. Only a call that differs from an earlier one
    in **nothing but a dimension** is refused: pad the same profile in another
    direction, or with an offset, or to a different limit, and it is a real
    second feature and passes. `CLAUDE.md` is explicit that an over-refusal is
    not safe, because the agent's recovery from one becomes a wrongly built part
    — so this refuses only the case where there is a right answer to point at,
    and it points at it.
    """
    for entry in context.journal:
        if entry.tool != tool:
            continue
        if str(entry.arguments.get("sketch") or "") != sketch:
            continue
        distinguishing = {
            key
            for key in set(entry.arguments) | set(arguments)
            if key not in _DIMENSION_ARGUMENTS
            and entry.arguments.get(key) != arguments.get(key)
        }
        if distinguishing:
            continue
        owner = entry.feature or tool.removeprefix("catia_")
        dimension = next(
            (key for key in arguments if key in _DIMENSION_ARGUMENTS and key != "name"),
            "length_mm",
        )
        separator = chr(92)
        raise GeometryError(
            f"{sketch!r} has already been built into {owner!r} by {tool}, and this "
            f"call differs from that one only in its size. Building it again would "
            f"leave the part carrying two features where one was meant, and the "
            f"second would sit on top of the first. To change the size, call "
            f"catia_set_parameter with name='{owner}{separator}{dimension}' and "
            f"unit='{_unit_for(dimension)}' — it "
            "rewrites the call that built the feature and rebuilds the part from the "
            "top. If you did mean a second, separate feature, say what makes it "
            "different: a direction, an offset, or a different limit."
        )


def _unit_for(argument: str) -> str:
    """The unit `catia_set_parameter` will demand for this argument.

    Included in the advice because that tool requires `unit` and requires it for
    a real reason — a parameter is typed, and setting a length in degrees is a
    silent no-op that leaves the model looking unchanged with nothing to explain
    why. Measured at gate G1: told to use catia_set_parameter and given the name
    but not the unit, the agent called it without one, was refused, and only got
    it right on the third attempt. Advice that is nearly complete costs a call.
    """
    from app.kernel.occt.operations.parameters import unit_of

    return unit_of(argument)


def _no_sketch_message(
    context: BuildContext, arguments: Mapping[str, Any], tool: str
) -> str:
    """Why there is no sketch — and, when it is obvious, what was meant instead.

    **This message used to cause the wrong recovery, measured at gate G1 on
    2026-09-06.** Told to adjust a plate's thickness until it weighed 2.4 kg, the
    agent called `catia_pad {"feature": "Pad.1", "length_mm": 11}` — which is
    unmistakably *"make Pad.1 eleven millimetres"* — and was told
    `catia_pad needs a sketch to build from, and none was named.` So it supplied
    a sketch: it renamed `Pad.1` out of the way and **padded the same sketch a
    second time**, leaving the document carrying two pads. Every call after the
    refusal was a correct response to the sentence it was given, and the sentence
    was answering a different question from the one being asked.

    `CLAUDE.md` says an agent's recovery from a refusal is what turns a refusal
    into a wrongly built part, and that a message must say what to do next. A
    `feature` argument naming something that already exists, with no sketch, has
    exactly one sensible reading, and the tool for it exists — so the message
    names it, with the parameter spelled the way `catia_list_parameters` spells
    it, because a name the model cannot type is a tool it cannot call.
    """
    wanted = str(arguments.get("feature") or "").strip()
    document = context.document
    known = set(document.feature_names()) if document is not None else set()

    if wanted and wanted in known:
        dimensions = [
            key
            for key, value in arguments.items()
            if key != "feature" and isinstance(value, (int, float))
        ]
        dimension = dimensions[0] if dimensions else "length_mm"
        separator = chr(92)
        return (
            f"{tool} builds a new feature from a sketch; it does not change one that "
            f"already exists, and {wanted!r} already exists. To change a dimension of "
            f"{wanted!r}, call catia_set_parameter with "
            f"name='{wanted}{separator}{dimension}', unit='{_unit_for(dimension)}' — "
            "that rewrites the call that built "
            "it and rebuilds the part from the top, so everything downstream moves with "
            "it. Call catia_list_parameters to see the names. Padding the same sketch "
            "again would leave the part carrying two features instead of one changed one."
        )

    if wanted:
        listed = ", ".join(sorted(known)) or "nothing yet"
        return (
            f"{tool} needs a sketch to build from, and none was named. It was given "
            f"feature={wanted!r}, which is not a feature of this part — this part has: "
            f"{listed}. If you meant to build something new, name the sketch to build "
            "from; if you meant to change something that exists, call "
            "catia_list_parameters to see what can be changed."
        )

    return f"{tool} needs a sketch to build from, and none was named."


def _reject_unsupported_limit(arguments: Mapping[str, Any], tool: str) -> None:
    limit = arguments.get("limit")
    if not limit:
        return
    reason = _UNSUPPORTED_LIMITS.get(str(limit))
    if reason is not None:
        raise OperationNotSupported(f"{tool} with limit={limit!r}", reason)


# -- limits resolved against the geometry -------------------------------------


def _limited_distance(
    context: BuildContext, arguments: Mapping[str, Any], tool: str, *, adds_material: bool
) -> float | None:
    """How far to extrude when `limit` names a stopping place rather than a distance.

    Returns None when the operation was given a plain length, which is every call that
    does not use a limit.

    **`up_to_next` means different things for a pad and a pocket, and that is not a
    quirk of this implementation.** A pad grows *until it runs into* the next wall, so it
    stops at the near side of the material ahead of it. A pocket cuts *until it breaks
    out of* the wall it is in, so it stops at the far side. Implementing one meaning for
    both would make every pocket stop the moment it touched the face it was drilling.

    The stopping distances are read from the material the extrusion actually passes
    through, decomposed into separate solids so "the next one" is a real question. Each
    solid's extent along the extrusion direction is measured in a frame rotated to put
    that direction on +Z — exact for the analytic surfaces a machined part is made of,
    and an upper bound on a spline, where OCCT's box is built from control points.
    """
    limit = str(arguments.get("limit") or "")
    if limit not in _COMPUTED_LIMITS:
        return None

    document = context.require_document()
    sketch = _sketch_argument(context, arguments, tool)
    direction = sketch.frame().Direction()
    if arguments.get("reversed"):
        direction = direction.Reversed()

    if limit == "up_to_plane":
        return _distance_to_plane(document, arguments, sketch, direction, tool)

    material = document.shape
    if material is None:
        raise GeometryError(
            f"{tool} with limit={limit!r} stops against existing material, and this part "
            "has none yet. Give an explicit length for the first feature."
        )

    reach = _through_all_depth(context)
    probe = _prism(sketch.face(), direction, reach, tool)
    common = symbol("BRepAlgoAPI_Common")(probe, material)
    if not common.IsDone():
        raise GeometryError(
            f"{tool} with limit={limit!r} could not work out what the extrusion passes "
            "through. Check that the profile lies over the part."
        )

    spans = _spans_along(common.Shape(), sketch, direction)
    if not spans:
        raise GeometryError(
            f"{tool} with limit={limit!r} found no material ahead of the profile, so "
            "there is nothing to stop against. Give an explicit length, or move the "
            "profile over the part."
        )

    if limit == "up_to_last":
        # The far side of the furthest material: the extrusion clears everything.
        return max(far for _, far in spans)

    # up_to_next, whose meaning depends on the direction of the operation.
    if adds_material:
        ahead = [near for near, _ in spans if near > LIMIT_TOLERANCE_MM]
        if not ahead:
            raise GeometryError(
                f"{tool} with limit='up_to_next' found nothing ahead of the profile to "
                "stop against — the material it starts on is the only thing in the way. "
                "Use 'up_to_last' to pass through it, or give a length."
            )
        return min(ahead)

    first_far = min(spans, key=lambda span: span[0])[1]
    if first_far <= LIMIT_TOLERANCE_MM:  # pragma: no cover - a zero-thickness solid
        raise GeometryError(
            f"{tool} with limit='up_to_next' measured no thickness to cut through."
        )
    return first_far


def _distance_to_plane(
    document: Any, arguments: Mapping[str, Any], sketch: Sketch, direction: Any, tool: str
) -> float:
    """How far from the sketch to a named plane, along the extrusion direction.

    Refused rather than signed-flipped when the plane lies *behind* the profile: an
    extrusion of negative length is not what the design asked for, and silently taking
    the magnitude would build the feature on the wrong side of the sketch.
    """
    from app.kernel.occt.elements import plane_frame

    named = arguments.get("up_to") or arguments.get("plane")
    if not named:
        raise GeometryError(
            f"{tool} with limit='up_to_plane' needs up_to to name the plane to stop at."
        )
    target = plane_frame(document, named, tool=tool)

    origin = sketch.frame().Location()
    anchor = target.Location()
    normal = target.Direction()

    offset = (anchor.X() - origin.X(), anchor.Y() - origin.Y(), anchor.Z() - origin.Z())
    along_normal = offset[0] * normal.X() + offset[1] * normal.Y() + offset[2] * normal.Z()
    travel = direction.X() * normal.X() + direction.Y() * normal.Y() + direction.Z() * normal.Z()

    if abs(travel) < LIMIT_TOLERANCE_MM:
        raise GeometryError(
            f"{tool} cannot stop at {named!r}: that plane is parallel to the extrusion "
            "direction, so the extrusion never reaches it."
        )

    distance = along_normal / travel
    if distance <= LIMIT_TOLERANCE_MM:
        raise GeometryError(
            f"{tool} cannot stop at {named!r}: it lies behind the profile, so the "
            f"extrusion would have to run backwards ({distance:.3f} mm). Set reversed, "
            "or name a plane on the other side."
        )
    return distance


def _spans_along(
    shape: Any, sketch: Sketch, direction: Any
) -> list[tuple[float, float]]:
    """Each solid's (near, far) extent along the direction, measured from the sketch.

    One entry per connected solid, so "the next one" is answerable. Sorted by near edge
    because every caller wants them in the order the extrusion meets them.
    """
    from app.kernel.occt.topology import SOLID, explore

    origin = sketch.frame().Location()

    # Move the world into the sketch's frame, so the box's z range *is* the distance
    # from the profile along the extrusion direction.
    #
    # **The single-argument `SetTransformation` is the one that does this.** The
    # two-argument form maps between two named systems and leaves the result in world
    # coordinates here — which measured absolute Z instead of distance-from-the-sketch,
    # and was wrong by exactly the sketch's height. It was caught by a pad that bridged
    # a 20 mm gap with 30 mm of material; the pocket case that shares this code got the
    # right number from the wrong span and would have shipped looking correct.
    to_local = symbol("gp_Trsf")()
    to_local.SetTransformation(symbol("gp_Ax3")(origin, direction))

    spans: list[tuple[float, float]] = []
    for solid in explore(shape, SOLID):
        aligned = symbol("BRepBuilderAPI_Transform")(solid, to_local, True).Shape()
        box = symbol("Bnd_Box")()
        symbol("BRepBndLib").AddOptimal_s(aligned, box, True, True)
        if box.IsVoid():  # pragma: no cover - an empty solid is not returned by explore
            continue
        _, _, near, _, _, far = box.Get()
        spans.append((near, far))

    return sorted(spans)


def _through_all_depth(context: BuildContext) -> float:
    """Deep enough to pass through whatever exists, from wherever it starts.

    The part's bounding-box diagonal is the shortest length guaranteed to clear it from
    any direction, doubled so a profile starting outside the part still passes through.
    Computed from the geometry rather than being a large constant, because a constant
    that is generous for a bracket is not generous for a chassis.
    """
    from app.kernel.occt.metrology import bounding_box_mm

    document = context.require_document()
    if document.shape is None:
        raise GeometryError(
            "A through-all pocket needs material to cut through, and this part is empty."
        )
    size = bounding_box_mm(document.shape)["size"]
    return 2.0 * math.sqrt(sum(component * component for component in size))


def solid_combine(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The common volume of two profiles extruded in different directions.

    CATIA's Solid Combine, and the cheapest way to author a shape that is awkward from
    any single sketch: a cam profile from the front and its width from the side intersect
    to give the cam. Two views, one solid, no lofting.

    **Each profile is extruded both ways from its own plane**, not just forwards. The
    intersection is what carries the shape, so a one-sided extrusion would make the
    result depend on which side of the world origin each sketch happened to sit — the
    same profile pair would combine or miss entirely depending on a number nobody chose.
    """
    document = context.require_document()

    first = document.sketch(str(arguments["first_profile"]))
    second = document.sketch(str(arguments["second_profile"]))

    reach = _combine_reach(first, second)
    left = _swept_both_ways(first, _combine_direction(arguments.get("first_direction"), first), reach)
    right = _swept_both_ways(
        second, _combine_direction(arguments.get("second_direction"), second), reach
    )

    maker = symbol("BRepAlgoAPI_Common")(left, right)
    blank = build_or_raise(
        maker,
        tool=SOLID_COMBINE,
        detail="The two extruded profiles do not overlap, so their common volume is "
        "empty. Check that the profiles cover the same region when seen along each "
        "other's direction.",
    )
    if not _encloses_solid(blank):
        raise GeometryError(
            f"{SOLID_COMBINE} produced no solid: the two profiles, extruded along their "
            "directions, share no volume."
        )
    return combine_into_part(context, document, arguments, SOLID_COMBINE, blank, adds_material=True)


def _combine_direction(value: Any, sketch: Sketch) -> Any:
    """The direction a combine profile is extruded along — its own normal by default."""
    if value is None:
        return sketch.frame().Direction()
    vector = as_point(value, argument="direction")
    if sum(component * component for component in vector) < 1e-12:
        return sketch.frame().Direction()
    return symbol("gp_Dir")(*vector)


def _combine_reach(first: Sketch, second: Sketch) -> float:
    """Far enough that both extrusions certainly cross each other.

    Derived from the profiles themselves rather than being a constant: a reach generous
    for a cam is not generous for a chassis rail, and one too short silently trims the
    combined solid instead of failing.
    """
    from app.kernel.occt.metrology import bounding_box_mm

    span = 0.0
    for sketch in (first, second):
        size = bounding_box_mm(sketch.face())["size"]
        centre = bounding_box_mm(sketch.face())["max"]
        span = max(span, math.sqrt(sum(v * v for v in size)), math.sqrt(sum(v * v for v in centre)))
    return 2.0 * max(span, 1.0)


def _swept_both_ways(sketch: Sketch, direction: Any, reach: float) -> Any:
    """A profile prismed `reach` in each direction from its own plane."""
    face = _translated(sketch.face(), direction, -reach)
    return _prism(face, direction, 2.0 * reach, SOLID_COMBINE)


__all__ = [
    "GROOVE",
    "PAD",
    "POCKET",
    "SHAFT",
    "SOLID_COMBINE",
    "groove",
    "pad",
    "pocket",
    "shaft",
    "solid_combine",
]
