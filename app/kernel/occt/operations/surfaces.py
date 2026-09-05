"""Generative Shape Design: geometry that is skin rather than material.

Everything in `features.py`, `sweeps.py` and `dressup.py` builds or modifies *material*.
These build **surfaces** — geometry with area and no volume — and the two operations that
carry a surface back across into material. That seam is the point of the module: a duct,
a bottle, a moulded cover and a wing are all far easier to describe as a skin than as a
solid, and the way a real part gets modelled is skin first, thicken or close last.

Three things about the arrangement here are decisions rather than convenience.

**A surface is not a body, and the document keeps them apart.** A constructed surface goes
into `PartDocument`'s construction store, never into `_bodies`; the part's mass and volume
are exactly what they were before it was built. Making a surface the active body would
report a part with no solid, which reads like a failed feature rather than like a skin
waiting for `catia_close_surface`.

**A thickened surface comes out of OCCT inside-out, and that must be corrected rather
than measured around.** `MakeThickSolidBySimple` with a positive offset returns a solid
whose faces point inward: its signed volume is negative, `BRepCheck_Analyzer` calls it
valid, and — the part that matters — **a later fuse silently returns the wrong answer**
rather than failing. Fusing a 1,000 mm³ block onto a −4,800 mm³ plate measured −4,800:
the block did not error, it was swallowed. `_outward` is where that is fixed, and it is
fixed by the definition of the defect, not by taking an absolute value; see its docstring.

**What a surface operation cannot yet do is refused with the reason.** Guides on a loft, a
tangent-continuous fill, tangent propagation on an extract: each is a real GSD capability
and each is named in the refusal with what it would take, rather than being silently
ignored — an ignored `guides` argument builds a different shape from the one asked for and
nothing says so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.elements import axis_for
from app.kernel.occt.naming import record_primitive
from app.kernel.occt.operations.context import (
    BuildContext,
    as_direction,
    as_point,
    as_positive_length,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.selectors import SUB_ENTITY_MARK, select_edges, select_faces
from app.kernel.occt.sketching import Sketch
from app.kernel.occt.topology import (
    EDGE,
    FACE,
    SHELL,
    SOLID,
    compound,
    connected_pieces,
    count,
    edges,
    explore,
    faces,
)

EXTRUDE = "catia_surface_extrude"
REVOLVE = "catia_surface_revolve"
OFFSET = "catia_surface_offset"
FILL = "catia_surface_fill"
LOFT = "catia_surface_loft"
JOIN = "catia_join"
EXTRACT = "catia_extract"
BOUNDARY = "catia_boundary"
CLOSE = "catia_close_surface"
THICKEN = "catia_thick_surface"

#: What the construction store calls each kind. `Construction.kind` carries one of these.
SURFACE: Final = "surface"
CURVE: Final = "curve"

#: Sewing tolerance when a caller names none. Tight enough that two surfaces built from
#: the same construction are joined and two that genuinely do not meet are not — a
#: generous default silently bridges a gap the design has a real reason to care about.
DEFAULT_SEWING_TOLERANCE_MM: Final = 1e-6

#: Distance below which two offset surfaces are the same surface. Used only to refuse a
#: zero offset, which is a no-op dressed as an operation.
MIN_OFFSET_MM: Final = 1e-9


# -- creating surfaces --------------------------------------------------------


def surface_extrude(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sweep a curve along a straight direction — the surface counterpart of a pad."""
    document = context.require_document()
    profile = _curve_named(document, arguments.get("profile"), tool=EXTRUDE, argument="profile")
    direction = as_direction(arguments.get("direction"), argument="direction")
    length = as_positive_length(arguments.get("length_mm"), argument="length_mm", tool=EXTRUDE)

    second = float(arguments.get("second_length_mm") or 0.0)
    if arguments.get("symmetric"):
        # Half each way, so the *total* is the length that was asked for — the same rule
        # `catia_pad` follows, and it must be the same or the two operations disagree
        # about what one number means.
        back, forward = length / 2.0, length / 2.0
    else:
        back, forward = abs(second), length

    start = _translated(profile, _scaled(direction, -back)) if back else profile
    sheet = _prism(start, _scaled(direction, back + forward))
    return _record(context, document, arguments, EXTRUDE, sheet, SURFACE, "extrude")


def surface_revolve(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Revolve a curve about an axis — a cone, a dished end, any skin of revolution.

    Unlike `catia_shaft` the profile need not be closed, which is the whole reason to
    reach for this: a closed profile revolved is a solid and belongs in Part Design.
    """
    document = context.require_document()
    profile = _curve_named(document, arguments.get("profile"), tool=REVOLVE, argument="profile")
    axis = axis_for(document, arguments.get("axis"), tool=REVOLVE)

    angle = float(arguments.get("angle_deg") or 360.0)
    second = float(arguments.get("second_angle_deg") or 0.0)
    total = angle + second
    if not 0.0 < total <= 360.0:
        raise GeometryError(
            f"{REVOLVE} needs a revolution angle between 0 and 360 degrees; "
            f"{angle}° plus {second}° is {total}°."
        )

    if second:
        # Revolving backwards first and then forwards would build two surfaces that have
        # to be sewn; turning the axis back by the second angle and sweeping the total
        # once builds one, which is what the caller asked for.
        turned = _rotated(profile, axis, -second)
    else:
        turned = profile

    maker = symbol("BRepPrimAPI_MakeRevol")(turned, axis, math.radians(total))
    skin = build_or_raise(
        maker,
        tool=f"{REVOLVE} through {total}°",
        detail="A profile that crosses its own revolution axis cannot be revolved — "
        "move it clear of the axis.",
    )
    return _record(context, document, arguments, REVOLVE, skin, SURFACE, "revolve")


def surface_offset(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """A surface parallel to an existing one, at a distance along its own normal.

    Offsetting fails where the distance exceeds a local radius of curvature, because the
    offset surface would cross itself. OCCT reports that as a refusal rather than as a
    self-intersecting shape, which is why the error names the curvature rather than
    suggesting the input was malformed.
    """
    document = context.require_document()
    source = _surface_named(document, arguments.get("surface"), tool=OFFSET, argument="surface")
    distance = float(arguments.get("distance_mm") or 0.0)
    if abs(distance) < MIN_OFFSET_MM:
        raise GeometryError(
            f"{OFFSET} needs a distance to offset by, and {distance} would return the "
            "same surface. Give a non-zero distance_mm."
        )
    if arguments.get("reversed"):
        distance = -distance

    if arguments.get("both_sides"):
        raise OperationNotSupported(
            f"{OFFSET} with both_sides",
            "one call makes one surface here. Call it twice, once with reversed, and "
            "join the two if they are wanted as a single element",
        )

    offset = _offset_surface(source, distance, tool=OFFSET)
    return _record(context, document, arguments, OFFSET, offset, SURFACE, "offset")


def surface_fill(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Patch a closed boundary of curves with a surface.

    How a hole in a skin is closed. **Continuity beyond `point` is refused**, because it
    is not the fill that provides it: matching a patch tangentially to its neighbours
    needs each boundary edge handed to OCCT together with the face it lies on, and
    resolving "the surface this curve came off" is not something a name alone answers.
    A point-continuous patch that says so beats a tangent one that is not.
    """
    document = context.require_document()
    boundary = _references(arguments.get("boundary"), tool=FILL, argument="boundary", minimum=1)

    continuity = str(arguments.get("continuity") or "point").lower()
    if continuity != "point":
        raise OperationNotSupported(
            f"{FILL} with continuity={continuity!r}",
            "matching a patch to its neighbours needs each boundary edge paired with "
            "the face it lies on, which a curve's name does not carry. Use "
            "continuity='point', or build the neighbours' shared edges with "
            "catia_boundary and fill against those",
        )
    if arguments.get("supports"):
        raise OperationNotSupported(
            f"{FILL} with supports",
            "supports exist to carry continuity, and only point continuity is "
            "available here, where they change nothing",
        )

    outline = [
        edge
        for reference in boundary
        for edge in edges(_named_geometry(document, reference, tool=FILL, argument="boundary"))
    ]
    if not outline:
        raise GeometryError(
            f"{FILL} needs a boundary of curves to patch and the elements named hold no "
            "edge at all. Name the curves that enclose the opening, in any order."
        )

    point = arguments.get("passing_point")
    if point is None:
        # A flat opening has an exact answer, and it is worth taking — see `_flat_patch`.
        # A passing point is what rules it out: a planar face cannot be made to pass
        # through somewhere off its own plane.
        flat = _flat_patch(outline)
        if flat is not None:
            return _record(context, document, arguments, FILL, flat, SURFACE, "fill")

    maker = symbol("BRepOffsetAPI_MakeFilling")()
    c0 = symbol("GeomAbs_Shape").GeomAbs_C0
    for edge in outline:
        maker.Add(edge, c0)
    if point is not None:
        maker.Add(symbol("gp_Pnt")(*as_point(point, argument="passing_point")))

    patch = build_or_raise(
        maker,
        tool=FILL,
        detail="The boundary does not enclose an area — check that the curves meet end "
        "to end, and that they are not all in one straight line.",
    )
    return _record(context, document, arguments, FILL, patch, SURFACE, "fill")


def surface_loft(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Loft a surface through a series of section curves — a Multi-sections Surface.

    **Guides and a spine are refused rather than ignored**, and that distinction is the
    reason this operation is honest: a loft steered by two guides is a different shape
    from the same sections lofted freely, so accepting the argument and dropping it would
    build the wrong surface and report success.
    """
    document = context.require_document()
    sections = _references(arguments.get("sections"), tool=LOFT, argument="sections", minimum=2)

    for unsupported, reason in (
        (
            "guides",
            "steering a loft between its sections needs a swept construction with the "
            "guides as rails, which is a different algorithm from the section loft this "
            "builds. Add intermediate sections to control the shape instead",
        ),
        (
            "spine",
            "a spine orients each section against a curve rather than against its "
            "neighbours, which the section loft has no way to be told. Place the "
            "sections on planes normal to the path you had in mind",
        ),
    ):
        if arguments.get(unsupported):
            raise OperationNotSupported(f"{LOFT} with {unsupported}", reason)

    maker = symbol("BRepOffsetAPI_ThruSections")(False, False)
    if arguments.get("closed"):
        maker.SetContinuity(symbol("GeomAbs_Shape").GeomAbs_C0)
    for reference in sections:
        maker.AddWire(
            _wire_of(
                _named_geometry(document, reference, tool=LOFT, argument="sections"),
                reference=reference,
                tool=LOFT,
            )
        )

    lofted = build_or_raise(
        maker,
        tool=LOFT,
        detail="The sections could not be lofted through. They must each be a single "
        "closed or open wire, and they must not cross one another.",
    )
    return _record(context, document, arguments, LOFT, lofted, SURFACE, "loft")


# -- operations on surfaces ---------------------------------------------------


def join(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sew surfaces or curves into one element.

    Almost every downstream operation — close, thicken, split, a solid boolean — wants
    one surface rather than fourteen, so this is the step between building a skin in
    pieces and using it. `tolerance_mm` is what bridges the small gaps imported data
    arrives with, and it defaults tight: a generous default silently closes a gap the
    design has a real reason to know about.

    `check_connexity` defaults **on**, matching CATIA. A join that quietly returned two
    disconnected shells would be found by whatever failed next, several operations later.
    """
    document = context.require_document()
    references = _references(arguments.get("elements"), tool=JOIN, argument="elements", minimum=2)
    tolerance = float(arguments.get("tolerance_mm") or DEFAULT_SEWING_TOLERANCE_MM)

    pieces = [
        _named_geometry(document, reference, tool=JOIN, argument="elements")
        for reference in references
    ]
    curves = [piece for piece in pieces if count(piece, FACE) == 0]
    if curves and len(curves) != len(pieces):
        raise GeometryError(
            f"{JOIN} was given both curves and surfaces. Join curves to curves and "
            "surfaces to surfaces — one element cannot be both."
        )

    if curves:
        # Curves are collected rather than sewn: sewing is a face operation, and a joined
        # curve is exactly "these edges, addressed as one", which a compound already is.
        joined, kind = compound(pieces), CURVE
    else:
        sewing = symbol("BRepBuilderAPI_Sewing")(tolerance)
        for piece in pieces:
            sewing.Add(piece)
        sewing.Perform()
        joined, kind = sewing.SewedShape(), SURFACE

    if joined is None or joined.IsNull():
        raise GeometryError(
            f"{JOIN} produced nothing from {len(pieces)} elements. Check that they are "
            "the elements you meant, and that they are not all empty."
        )

    if arguments.get("check_connexity") is not False and kind == SURFACE:
        pieces_left = connected_pieces(joined)
        if pieces_left != 1:
            raise GeometryError(
                f"{JOIN} left {pieces_left} separate pieces rather than one connected "
                f"surface, with a tolerance of {tolerance:g} mm. The elements do not all "
                "meet: widen tolerance_mm if the gaps are small and expected, or pass "
                "check_connexity: false to keep them as they are."
            )

    return _record(context, document, arguments, JOIN, joined, kind, "join")


def extract(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Take faces or edges off existing geometry as an element of their own.

    This is how a surface is derived from a solid so it can be offset, trimmed or lofted
    against without touching the solid — and it is where `feature#selector` earns its
    keep, because `slab#top` names the face across a regeneration that renumbers
    everything.
    """
    document = context.require_document()
    references = _references(arguments.get("elements"), tool=EXTRACT, argument="elements", minimum=1)

    propagation = str(arguments.get("propagation") or "none").lower()
    if propagation != "none":
        raise OperationNotSupported(
            f"{EXTRACT} with propagation={propagation!r}",
            "spreading from a seed face along its tangent chain is not something this "
            "backend walks yet. Name the faces you want — catia_list_faces reports the "
            "predicate that selects each one",
        )

    source = context.require_shape(EXTRACT)
    wanted: list[Any] = []
    kind = SURFACE
    for reference in references:
        found = _named_geometry(document, reference, tool=EXTRACT, argument="elements")
        if count(found, FACE) == 0:
            kind = CURVE
        wanted.append(found)

    if arguments.get("complementary"):
        # The complement is taken over the part's own faces, which is the only set the
        # word can mean: "everything except these" needs a *these* and a whole.
        if kind == CURVE:
            raise OperationNotSupported(
                f"{EXTRACT} with complementary over edges",
                "the complement is taken over the part's faces. Extract the faces you "
                "want, or name the edges directly",
            )
        selected = [face for piece in wanted for face in faces(piece)]
        wanted = [
            face
            for face in faces(source)
            if not any(face.IsSame(other) for other in selected)
        ]
        if not wanted:
            raise GeometryError(
                f"{EXTRACT} with complementary selected every face of the part, so the "
                "complement is empty. Name fewer faces."
            )

    taken = wanted[0] if len(wanted) == 1 else compound(wanted)
    return _record(context, document, arguments, EXTRACT, taken, kind, "extract")


def boundary(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """The free boundary of a surface, as a curve.

    The usual first step in patching a gap or building a blend. **Free** boundary: an
    edge shared by two faces of the same surface is interior and is not part of it, which
    is what makes the boundary of a sewn skin the outline of the skin rather than every
    edge in it.
    """
    document = context.require_document()
    source = _surface_named(document, arguments.get("surface"), tool=BOUNDARY, argument="surface")

    propagation = str(arguments.get("propagation") or "complete").lower()
    if propagation != "complete":
        raise OperationNotSupported(
            f"{BOUNDARY} with propagation={propagation!r}",
            "this returns the whole free boundary. Split the result with catia_split, "
            "or extract the edges you want by selector",
        )
    for limit in ("limit_from", "limit_to"):
        if arguments.get(limit):
            raise OperationNotSupported(
                f"{BOUNDARY} with {limit}",
                "trimming the boundary between two points needs the curve split at "
                "each, which catia_split will do once it lands. The whole boundary is "
                "available now",
            )

    analyser = symbol("ShapeAnalysis_FreeBounds")(source)
    wires = list(explore(analyser.GetClosedWires(), EDGE)) + list(
        explore(analyser.GetOpenWires(), EDGE)
    )
    if not wires:
        raise GeometryError(
            f"{BOUNDARY} found no free boundary on {arguments.get('surface')!r}. A "
            "closed surface has none — every edge is shared by two of its faces."
        )

    outline = compound(wires)
    return _record(context, document, arguments, BOUNDARY, outline, CURVE, "boundary")


# -- surface to solid ---------------------------------------------------------


def close_surface(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fill a closed surface with material — the seam between shape design and part design.

    The surface must genuinely be closed, and this refuses rather than repairs when it is
    not: an almost-closed skin filled anyway gives a solid with a hole in it that measures
    a plausible volume, which is worse than a refusal naming the gap.
    """
    document = context.require_document()
    source = _surface_named(document, arguments.get("surface"), tool=CLOSE, argument="surface")

    shells = explore(source, SHELL)
    if not shells:
        raise GeometryError(
            f"{CLOSE} needs a closed surface and was given one with no shell at all — a "
            "single unsewn face. Join the surface's pieces with catia_join first."
        )
    if len(shells) > 1:
        raise GeometryError(
            f"{CLOSE} was given {len(shells)} separate shells. Each would become its own "
            "solid, and which one is the part is not stated — join them with catia_join, "
            "or close them one at a time."
        )

    shell = symbol("TopoDS").Shell_s(shells[0])
    if not shell.Closed():
        raise GeometryError(
            f"{CLOSE} was given a surface that is not closed, so there is no inside to "
            "fill. Join every piece of the skin with catia_join, and if it still refuses, "
            "the gap catia_healing would close is the reason."
        )

    maker = symbol("BRepBuilderAPI_MakeSolid")(shell)
    solid = build_or_raise(
        maker,
        tool=CLOSE,
        detail="The shell is closed but could not be filled. It may be self-intersecting.",
    )
    return _into_the_part(context, document, arguments, CLOSE, _outward(solid))


def thick_surface(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Give an open surface a thickness, turning it into material.

    The other route from skin to solid, and the right one when the shape was designed as
    a shell — a panel, a moulded cover — rather than as a closed volume. Material grows
    along the surface's own normal; `second_thickness_mm` adds to the other side and
    `reversed` swaps which is which.
    """
    document = context.require_document()
    source = _surface_named(document, arguments.get("surface"), tool=THICKEN, argument="surface")

    first = as_positive_length(
        arguments.get("thickness_mm"), argument="thickness_mm", tool=THICKEN
    )
    second = float(arguments.get("second_thickness_mm") or 0.0)
    if second < 0.0:
        raise GeometryError(
            f"{THICKEN} takes second_thickness_mm as a thickness on the other side, so "
            f"it cannot be negative; got {second}. Use reversed to swap the sides."
        )
    if arguments.get("reversed"):
        first, second = second, first
        if first <= 0.0:
            raise GeometryError(
                f"{THICKEN} with reversed puts second_thickness_mm on the normal's side, "
                "and none was given — so the whole thickness would fall on one side "
                "anyway. Drop reversed, or give second_thickness_mm."
            )

    # The other side is reached by moving the surface back before thickening through the
    # total, rather than by thickening twice and fusing: one offset means one solid, and
    # the join two thickened halves would need is exactly where a sliver comes from.
    base = _offset_surface(source, -second, tool=THICKEN) if second else source

    maker = symbol("BRepOffsetAPI_MakeThickSolid")()
    try:
        maker.MakeThickSolidBySimple(base, first + second)
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(
            f"{THICKEN} could not thicken the surface: {exc}. A thickness larger than "
            "the surface's tightest internal radius makes the offset cross itself."
        ) from exc
    solid = build_or_raise(
        maker,
        tool=THICKEN,
        detail="A thickness larger than the surface's tightest internal radius makes "
        "the offset surface cross itself — reduce it, or thicken the other way.",
    )
    return _into_the_part(context, document, arguments, THICKEN, _outward(solid))


# -- construction -------------------------------------------------------------


def _flat_patch(outline: Sequence[Any]) -> Any | None:
    """The exact planar face over a closed, flat boundary — or None when there is not one.

    **`MakeFilling` approximates, even over a boundary that is dead flat.** It fits a
    B-spline through the edges, so a circular hole patched with it comes back with an
    area wrong in the seventh digit and a bounding box built from control points rather
    than from the patch — measured 314.1595 mm² against πr² = 314.1593, and a box half as
    big again as the disc. For the commonest case in the whole operation, a flat opening,
    the exact answer is one call away.

    Two things this must not do. It must not close the boundary itself:
    `MakeFace(wire, OnlyPlane=True)` reports **success** on an open wire, silently
    bridging the gap with an edge nobody drew, so the wire is required closed here rather
    than trusted to that check. And it must not force the planar case on a skew boundary
    — `NotPlanar` is exactly the answer that sends the caller to the approximation, which
    is the right tool for a boundary that genuinely is not flat.
    """
    wire = symbol("BRepBuilderAPI_MakeWire")()
    for edge in outline:
        wire.Add(edge)
    if not wire.IsDone():
        return None

    assembled = wire.Wire()
    if not assembled.Closed():
        return None

    face = symbol("BRepBuilderAPI_MakeFace")(assembled, True)
    return face.Face() if face.IsDone() else None


def _outward(solid: Any) -> Any:
    """The same solid, with its faces certainly pointing out of the material.

    **`MakeThickSolidBySimple` returns an inverted solid for a positive offset**, and it
    is not detectable by asking whether the shape is well formed: `BRepCheck_Analyzer`
    calls the inverted one valid, because it *is* a valid solid — it is the complement of
    the one that was wanted. What it is not is safe to fuse: fusing a 1,000 mm³ block onto
    an inverted 4,800 mm³ plate returns −4,800 mm³, no error, block gone.

    The test is the signed volume, and that is the definition rather than a symptom: by
    the divergence theorem the integral is negative exactly when the boundary normals
    point inward. Reversing flips every face's orientation at once, which is what puts
    them back. Done per solid so a surface that thickened into several pieces is fixed in
    all of them.
    """
    from app.kernel.occt.metrology import volume_mm3

    pieces = explore(solid, SOLID)
    if not pieces:
        return solid

    corrected = [
        piece.Reversed() if volume_mm3(piece) < 0.0 else piece for piece in pieces
    ]
    return corrected[0] if len(corrected) == 1 else compound(corrected)


def _into_the_part(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    solid: Any,
) -> Mapping[str, Any]:
    """Add a surface-derived solid to the part, through the one path every feature uses."""
    from app.kernel.occt.operations.features import combine_into_part

    return combine_into_part(context, document, arguments, tool, solid, adds_material=True)


def _record(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    shape: Any,
    kind: str,
    fallback: str,
) -> Mapping[str, Any]:
    """File a constructed surface or curve under the design's name for it.

    The naming labels are written even though a surface is not part of the body, so a
    shape-design feature has the same name history as every other feature and the two
    backends still report the same `Created(feature)` — the executor binds late-bound
    names from what this returns and must not be able to tell which kernel ran.
    """
    name = feature_name(arguments, fallback)
    feature = document.add_feature(name, tool)
    document.set_construction(feature, shape, name=name, kind=kind)
    record_primitive(feature.labels, shape)
    return context.result_for(feature)


# -- resolving what a name points at ------------------------------------------


def _named_geometry(document: Any, reference: Any, *, tool: str, argument: str) -> Any:
    """One named element as a shape: a constructed surface or curve, a sketch, or a selector.

    Four namespaces meet here, and a name that lands in two of them is **refused rather
    than resolved by table order** — the rule `app.kernel.occt.elements` already holds for
    measurement, for the same reason: picking one works until the day it silently builds
    from the wrong thing.
    """
    text = str(reference or "").strip()
    if not text:
        raise GeometryError(f"{tool} needs {argument} and none was named.")

    if SUB_ENTITY_MARK in text:
        return _selected(document, text, tool=tool)

    matches = [
        label
        for label, holds in (
            ("constructed surface or curve", document.has_construction),
            ("sketch", lambda name: name in document.sketches),
            # A constructed surface is *also* a feature under the same name — one tree
            # element, filed in two places — so it must not read as a collision with
            # itself. Same rule, same reason as `elements._namespaces_holding`.
            (
                "feature",
                lambda name: document.has_feature(name)
                and not document.has_construction(name),
            ),
        )
        if holds(text)
    ]
    if len(matches) > 1:
        raise GeometryError(
            f"{text!r} names more than one thing in this part ({', '.join(matches)}), so "
            f"what {tool} should build from is ambiguous. Rename one of them."
        )
    if not matches:
        raise GeometryError(
            f"{tool} was given {argument}={text!r} and there is nothing by that name in "
            f"this part. {_available(document)}"
        )

    found = matches[0]
    if found == "constructed surface or curve":
        return document.construction(text).shape
    if found == "sketch":
        return _sketch_geometry(document.sketch(text), tool=tool)
    return document.body(text)


def _selected(document: Any, text: str, *, tool: str) -> Any:
    """`feature#selector` → the faces or edges it names, as one shape.

    Which of the two a word means is decided by `elements.EDGE_WORDS`, imported rather
    than repeated: a second copy of that vocabulary would drift, and the symptom would be
    a selector that means edges to a measurement and faces to an extract.
    """
    from app.kernel.occt.elements import EDGE_WORDS

    part = document.shape
    if part is None:
        raise GeometryError(
            f"{tool} was asked for {text!r}, but nothing has been built in "
            f"{document.name} yet."
        )

    _, _, word = text.partition(SUB_ENTITY_MARK)
    if word.strip().lower() in EDGE_WORDS:
        return compound(select_edges(part, text, tool=tool, document=document))
    return compound(select_faces(part, text, tool=tool, document=document))


def _sketch_geometry(sketch: Sketch, *, tool: str) -> Any:
    """What a sketch offers a surface operation: its one curve, closed or open.

    `Sketch.path` is the resolver, so a sketch holding two curves is refused by the same
    message a rib's spine gets rather than by a second rule that could disagree with it.
    """
    return sketch.path(tool=tool)


def _curve_named(document: Any, reference: Any, *, tool: str, argument: str) -> Any:
    """A named element that is a curve, refusing a surface by name rather than by silence."""
    shape = _named_geometry(document, reference, tool=tool, argument=argument)
    if count(shape, FACE):
        raise GeometryError(
            f"{tool} needs {argument} to be a curve and {reference!r} is a surface. "
            "Take its outline with catia_boundary, or name a sketch."
        )
    if not count(shape, EDGE):
        raise GeometryError(
            f"{tool} needs {argument} to be a curve and {reference!r} holds no edge at "
            "all. Draw it with catia_sketch_line, or build it with catia_boundary."
        )
    return shape


def _surface_named(document: Any, reference: Any, *, tool: str, argument: str) -> Any:
    """A named element that is a surface, refusing a curve the same way."""
    shape = _named_geometry(document, reference, tool=tool, argument=argument)
    if not count(shape, FACE):
        raise GeometryError(
            f"{tool} needs {argument} to be a surface and {reference!r} is a curve. "
            "Build a surface from it first — catia_surface_extrude, catia_surface_fill "
            "or catia_surface_loft."
        )
    return shape


def _wire_of(shape: Any, *, reference: Any, tool: str) -> Any:
    """One wire, for an algorithm that takes wires rather than shapes.

    A section handed to a loft must be a single wire; a compound of two is refused here
    by name rather than by OCCT, which reports it as a failure to build with nothing
    said about which section was wrong.
    """
    from app.kernel.occt.topology import WIRE

    wires = explore(shape, WIRE)
    if len(wires) == 1:
        return symbol("TopoDS").Wire_s(wires[0])
    if not wires:
        raise GeometryError(
            f"{tool} was given {reference!r} as a section and it holds no wire. A "
            "section is one closed or open curve."
        )
    raise GeometryError(
        f"{tool} was given {reference!r} as a section and it holds {len(wires)} separate "
        "wires. Each section must be a single curve — draw them on sketches of their own."
    )


def _references(value: Any, *, tool: str, argument: str, minimum: int) -> list[Any]:
    """A list-of-names argument, refused clearly when it is one name or none."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise GeometryError(
            f"{tool} needs {argument} as a list of names, for example "
            f'["outline", "inner"]; got {value!r}.'
        )
    listed = list(value)
    if len(listed) < minimum:
        raise GeometryError(
            f"{tool} needs at least {minimum} element(s) in {argument} and was given "
            f"{len(listed)}."
        )
    return listed


def _available(document: Any) -> str:
    """What this part *does* hold, so a mistyped name is one step from fixed."""
    groups = (
        ("surfaces and curves", document.construction_names()),
        ("sketches", document.sketch_names()),
        ("features", [feature.name for feature in document]),
    )
    listed = [f"{label}: {', '.join(names)}" for label, names in groups if names]
    return ("It holds " + "; ".join(listed) + ".") if listed else "It holds nothing yet."


# -- geometry helpers ---------------------------------------------------------


def _offset_surface(source: Any, distance: float, *, tool: str) -> Any:
    """A surface parallel to `source`, positive along its own normal."""
    maker = symbol("BRepOffsetAPI_MakeOffsetShape")()
    try:
        maker.PerformByJoin(source, distance, DEFAULT_SEWING_TOLERANCE_MM)
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(
            f"{tool} could not offset the surface by {distance} mm: {exc}. An offset "
            "larger than the surface's tightest internal radius makes it cross itself."
        ) from exc
    return build_or_raise(
        maker,
        tool=f"{tool} (offset by {distance:g} mm)",
        detail="An offset larger than the surface's tightest internal radius makes it "
        "cross itself — reduce the distance, or offset the other way.",
    )


def _prism(shape: Any, vector: tuple[float, float, float]) -> Any:
    return symbol("BRepPrimAPI_MakePrism")(shape, symbol("gp_Vec")(*vector)).Shape()


def _translated(shape: Any, vector: tuple[float, float, float]) -> Any:
    transformation = symbol("gp_Trsf")()
    transformation.SetTranslation(symbol("gp_Vec")(*vector))
    return symbol("BRepBuilderAPI_Transform")(shape, transformation, True).Shape()


def _rotated(shape: Any, axis: Any, degrees: float) -> Any:
    transformation = symbol("gp_Trsf")()
    transformation.SetRotation(axis, math.radians(degrees))
    return symbol("BRepBuilderAPI_Transform")(shape, transformation, True).Shape()


def _scaled(vector: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


__all__ = [
    "BOUNDARY",
    "CLOSE",
    "CURVE",
    "DEFAULT_SEWING_TOLERANCE_MM",
    "EXTRACT",
    "EXTRUDE",
    "FILL",
    "JOIN",
    "LOFT",
    "MIN_OFFSET_MM",
    "OFFSET",
    "REVOLVE",
    "SURFACE",
    "THICKEN",
    "boundary",
    "close_surface",
    "extract",
    "join",
    "surface_extrude",
    "surface_fill",
    "surface_loft",
    "surface_offset",
    "surface_revolve",
    "thick_surface",
]
