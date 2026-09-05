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

**What a surface operation cannot yet do is refused with the reason** — and the reason is
kept current. Guides on a loft, a spine, and a tangent-continuous fill were all on that
list and are now built; what is left is tangent propagation on an extract, `extrapolate`,
`sew_surface`, and G2 filling, which OCCT itself refuses. An ignored argument builds a
different shape from the one asked for and nothing says so, so nothing here is accepted
and dropped.

**And what OCCT reports is not always what it did.** A tangent fill against a support that
stands square to the opening — a cylinder's wall at its own rim — returns `IsDone()` true
and a flat patch that is 90° from tangent all the way round. So `surface_fill` measures the
tangency it achieved against the support and refuses a patch that missed, rather than
filing one under a name that claims it is smooth.

**Which side of a cut survives is stated, not implied.** `catia_split` and `catia_trim`
both have to answer "which piece did you mean", and CATIA answers it by where the user
clicked. There is no click here, so the rule is written down instead: pieces are ordered
by the signed distance of their centre from the cutting element's plane, `first` is the
side its normal points away from and `second` is the side it points at. A cutter with no
plane has no such order, and that is refused by name rather than resolved by whichever
piece OCCT happened to list first — an answer that would be right half the time and
silently change when the geometry moved.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.elements import axis_for, face_centre
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
    domains,
    edges,
    explore,
    faces,
    has_solid,
)

EXTRUDE = "catia_surface_extrude"
REVOLVE = "catia_surface_revolve"
OFFSET = "catia_surface_offset"
FILL = "catia_surface_fill"
LOFT = "catia_surface_loft"
JOIN = "catia_join"
EXTRACT = "catia_extract"
BOUNDARY = "catia_boundary"
SPLIT = "catia_split"
TRIM = "catia_trim"
UNTRIM = "catia_untrim"
DISASSEMBLE = "catia_disassemble"
HEALING = "catia_healing"
ANALYSIS = "catia_surface_analysis"
CLOSE = "catia_close_surface"
SEW = "catia_sew_surface"
THICKEN = "catia_thick_surface"

#: What the construction store calls each kind. `Construction.kind` carries one of these.
SURFACE: Final = "surface"
CURVE: Final = "curve"

#: Sewing tolerance when a caller names none. Tight enough that two surfaces built from
#: the same construction are joined and two that genuinely do not meet are not — a
#: generous default silently bridges a gap the design has a real reason to care about.
DEFAULT_SEWING_TOLERANCE_MM: Final = 1e-6

#: How the continuities are spelled in the registry, and what OCCT calls each. `G1` is
#: *geometric* tangency — of the shape, not of the parameterisation — which is what an
#: engineer looking at a reflection means by smooth. `curvature` has no entry because
#: `BRepFill_Filling` refuses `GeomAbs_G2` outright in this build; see `surface_fill`.
_CONTINUITY_ORDER: Final[dict[str, str]] = {
    "point": "GeomAbs_C0",
    "tangent": "GeomAbs_G1",
}

#: Points per boundary edge at which a tangent fill's achieved tangency is measured. The
#: answer is therefore **sampled** — a patch could in principle wander between samples —
#: but the failure this catches is not subtle: OCCT gives up on a tangency it cannot reach
#: and returns a flat patch, which is 90° out everywhere rather than at one point.
_TANGENCY_SAMPLES: Final = 12

#: How far a "tangent" patch may actually miss tangency before the result is refused, in
#: degrees. Generous: a fill that works lands within a ten-thousandth of a degree, and the
#: one that does not is out by ninety.
_TANGENCY_TOLERANCE_DEG: Final = 1.0

#: How far two faces may turn across a shared edge and still count as a tangent join, in
#: degrees. A fillet meets its neighbour to machine precision, and the next-smallest angle
#: in a real part is a draft of half a degree, so this sits well below that.
_TANGENT_JOIN_TOLERANCE_DEG: Final = 0.05

#: Points along a shared edge at which two faces are compared for tangency. Three rather
#: than one as a hedge for freeform faces, where tangency can hold at one end of an edge
#: and not the other. **Nothing in the suite distinguishes one sample from three**: every
#: pair of analytic surfaces that meets tangentially at all does so along the whole edge,
#: which is what the test geometry is made of. Said plainly because a guard nobody has
#: seen fail is a guard nobody knows works — this one is a cheap precaution, not a
#: measured requirement.
_JOIN_SAMPLES: Final = 3

#: How far an extraction spreads from its seed faces, as the registry spells it.
_PROPAGATION_MODES: Final[frozenset[str]] = frozenset(
    {"none", "point_continuity", "tangent_continuity"}
)

#: How close two edges must be to be the same edge, in mm — one built by extraction and
#: the original it was taken from. Tight, because these are copies rather than neighbours:
#: anything looser starts matching a *different* edge of the same face.
_EDGE_MATCH_TOLERANCE_MM: Final = 1e-7

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
    """Patch a closed boundary of curves with a surface, optionally tangent to its neighbours.

    How a hole in a skin is closed, and `continuity` is what decides whether the repair
    shows. A `point` patch meets the opening and creases against it; a `tangent` one leaves
    each neighbour along that neighbour's own surface, which is the difference between a
    panel that reflects cleanly and one that does not.

    **Tangency is to a *face*, not to a curve**, so `supports` is not optional decoration:
    OCCT needs the boundary edge as it exists *on* the neighbouring face, carrying that
    face's parameter curve. Each boundary edge is therefore matched against the supports'
    own edges by geometry — same length, same midpoint — and the support's edge is what is
    handed over. Matching by position in the two lists would break the moment a caller
    ordered them differently, and nothing in the result would say so.

    **An unmatched edge is refused, and that refusal is load-bearing.** Handing OCCT an
    edge with no parameter curve on the face is documented to raise; in this binding it
    **segfaults during `Build()`** — `Add` accepts it quietly and the process dies with no
    exception to catch. So the check has to happen before the call, and no `try` anywhere
    could stand in for it.

    **And the tangency is measured on the result, because OCCT will report success without
    delivering it.** Filling the rim of a cylinder tangentially asks the patch to leave
    straight up, which the plate solver cannot reach: it returns `IsDone()` true and a flat
    disc that is 90° out all the way round. The same call on a spherical opening lands
    within a ten-thousandth of a degree. So the patch's normal is compared against the
    support's along the shared boundary, the answer rides in `tangent_error_deg`, and a
    patch that missed by more than a degree is refused rather than filed under a name that
    claims it is smooth.
    """
    document = context.require_document()
    boundary = _references(arguments.get("boundary"), tool=FILL, argument="boundary", minimum=1)

    continuity = str(arguments.get("continuity") or "point").lower()
    if continuity == "curvature":
        raise OperationNotSupported(
            f"{FILL} with continuity='curvature'",
            "OCCT's filling algorithm refuses G2 outright in this build — it answers "
            '"the continuity is not G0 G1 or G2" and builds nothing, so there is no '
            "curvature-continuous patch to be had here rather than a choice not to make "
            "one. Use continuity='tangent', which is measured and reported",
        )
    if continuity not in _CONTINUITY_ORDER:
        raise GeometryError(
            f"{FILL} takes continuity of point, tangent or curvature; got "
            f"{arguments.get('continuity')!r}."
        )

    supports = [
        face
        for reference in _references(
            arguments.get("supports") or [], tool=FILL, argument="supports", minimum=0
        )
        for face in faces(named_geometry(document, reference, tool=FILL, argument="supports"))
    ]
    if continuity != "point" and not supports:
        raise GeometryError(
            f"{FILL} was asked for {continuity} continuity with no supports. There is "
            "nothing for the patch to be tangent *to* — name the neighbouring surfaces in "
            "`supports`, or ask for continuity='point'."
        )

    outline = [
        edge
        for reference in boundary
        for edge in edges(named_geometry(document, reference, tool=FILL, argument="boundary"))
    ]
    if not outline:
        raise GeometryError(
            f"{FILL} needs a boundary of curves to patch and the elements named hold no "
            "edge at all. Name the curves that enclose the opening, in any order."
        )

    point = arguments.get("passing_point")
    if point is None and continuity == "point":
        # A flat opening has an exact answer, and it is worth taking — see `_flat_patch`.
        # A passing point rules it out, and so does tangency: a planar face cannot be made
        # to pass through somewhere off its own plane, nor to leave a curved neighbour
        # along that neighbour's surface.
        flat = _flat_patch(outline)
        if flat is not None:
            return _record(context, document, arguments, FILL, flat, SURFACE, "fill")

    order = getattr(symbol("GeomAbs_Shape"), _CONTINUITY_ORDER[continuity])
    maker = symbol("BRepOffsetAPI_MakeFilling")()
    matches = []
    for index, edge in enumerate(outline):
        if continuity == "point":
            maker.Add(edge, order)
            continue
        matched = _edge_on_a_support(edge, supports)
        if matched is None:
            raise GeometryError(
                f"{FILL} cannot make boundary curve {index + 1} {continuity}-continuous: "
                "it is not an edge of any surface named in `supports`, so there is no "
                "face for the patch to leave along. Extract the boundary from the "
                "neighbour with catia_boundary, name that neighbour as a support, or ask "
                "for continuity='point'."
            )
        maker.Add(matched[0], matched[1], order)
        matches.append(matched)
    if point is not None:
        maker.Add(symbol("gp_Pnt")(*as_point(point, argument="passing_point")))

    patch = build_or_raise(
        maker,
        tool=FILL,
        detail="The boundary does not enclose an area — check that the curves meet end "
        "to end, and that they are not all in one straight line.",
    )
    if continuity == "point":
        return _record(context, document, arguments, FILL, patch, SURFACE, "fill")

    missed = _worst_tangency_error(patch, matches)
    if missed > _TANGENCY_TOLERANCE_DEG:
        raise GeometryError(
            f"{FILL} was asked for a tangent patch and could only manage one {missed:.3g}° "
            "out along its boundary, so it is not tangent at all — OCCT reports success "
            "and returns a flat patch when it cannot reach the tangent it was given. That "
            "happens where the support stands square to the opening, as a cylinder's wall "
            "does at its own rim: the patch would have to leave straight up. Give a "
            "passing_point to lead it out, or fill with continuity='point' and blend "
            "separately."
        )
    return {
        **_record(context, document, arguments, FILL, patch, SURFACE, "fill"),
        "continuity": continuity,
        "tangent_error_deg": missed,
    }


def _worst_tangency_error(patch: Any, matches: Sequence[tuple[Any, Any]]) -> float:
    """How far the patch's normal turns away from its supports' along the shared boundary.

    Sampled — `_TANGENCY_SAMPLES` points per boundary edge — and the docstring on
    `surface_fill` says so. The absolute value of the dot product is taken because the two
    faces are neighbours: their outward normals can legitimately point opposite ways across
    a shared edge, and 180° apart is as tangent as 0°.
    """
    from app.kernel.occt.operations.curves import closest_face_normal

    worst = 0.0
    for edge, face in matches:
        adaptor = symbol("BRepAdaptor_Curve")(edge)
        first, last = adaptor.FirstParameter(), adaptor.LastParameter()
        for step in range(1, _TANGENCY_SAMPLES):
            place = adaptor.Value(first + (last - first) * step / _TANGENCY_SAMPLES)
            at = (place.X(), place.Y(), place.Z())
            _, _, on_patch = closest_face_normal(patch, at, tool=FILL)
            _, _, on_support = closest_face_normal(face, at, tool=FILL)
            aligned = abs(sum(a * b for a, b in zip(on_patch, on_support, strict=True)))
            worst = max(worst, math.degrees(math.acos(max(-1.0, min(1.0, aligned)))))
    return worst


def _edge_on_a_support(edge: Any, supports: Sequence[Any]) -> tuple[Any, Any] | None:
    """The support face carrying this edge, and the face's own copy of it.

    Matched on length and midpoint rather than by identity: the boundary the caller names
    is normally an *extracted* copy — `catia_boundary` builds a new edge — so identity
    never matches, and the copy carries no parameter curve on the face it came from. The
    support's own edge does, which is the whole reason for looking it up.
    """
    wanted_length, wanted_middle = _edge_signature(edge)
    for face in supports:
        for candidate in edges(face):
            length, middle = _edge_signature(candidate)
            if abs(length - wanted_length) > _EDGE_MATCH_TOLERANCE_MM:
                continue
            if max(abs(a - b) for a, b in zip(middle, wanted_middle, strict=True)) > (
                _EDGE_MATCH_TOLERANCE_MM
            ):
                continue
            return symbol("TopoDS").Edge_s(candidate), symbol("TopoDS").Face_s(face)
    return None


def _edge_signature(edge: Any) -> tuple[float, tuple[float, float, float]]:
    """An edge as (length, midpoint) — enough to recognise a copy of it."""
    adaptor = symbol("BRepAdaptor_Curve")(edge)
    middle = adaptor.Value((adaptor.FirstParameter() + adaptor.LastParameter()) / 2.0)
    properties = symbol("GProp_GProps")()
    symbol("BRepGProp").LinearProperties_s(edge, properties)
    return float(properties.Mass()), (middle.X(), middle.Y(), middle.Z())


def surface_loft(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Loft a surface through a series of section curves — a Multi-sections Surface.

    Two different algorithms sit behind one operation, and which runs is decided by
    whether the loft is *steered*:

    * **sections alone** — `ThruSections`, which interpolates from each section to the
      next and lets the shape go where that takes it;
    * **with a `spine`** — a swept construction: each section is carried along the spine
      and stays square to it. Two identical circles at the ends of a quarter arc give the
      torus segment Pappus predicts, where the free loft of the same two sections gives a
      straight tube 23% smaller. That is not a refinement, it is a different surface.
    * **with `guides`** — the same sweep, with a section scaled to keep contact with the
      guide. A circle of r=5 swept 60 mm along a guide flaring to 15 is the cone whose
      lateral area is `π(r₁+r₂)·slant`, to one part in 10⁶.

    **A guide without a spine is refused.** OCCT sweeps *along* a spine and steers with a
    guide, so with no spine there is nothing to sweep; computing a default spine from the
    sections would be inventing the curve the surface follows, which is the one thing a
    steered loft is asked to be told.
    """
    document = context.require_document()
    sections = _references(arguments.get("sections"), tool=LOFT, argument="sections", minimum=2)

    guides = _references(arguments.get("guides") or [], tool=LOFT, argument="guides", minimum=0)
    spine = arguments.get("spine")
    if guides and not spine:
        raise OperationNotSupported(
            f"{LOFT} with guides and no spine",
            "a steered loft is swept along a spine and pulled sideways by the guide, so "
            "with no spine there is nothing to sweep along. Working one out from the "
            "sections would mean inventing the curve the surface follows — name the "
            "spine, or drop the guide and add intermediate sections instead",
        )
    if len(guides) > 1:
        raise OperationNotSupported(
            f"{LOFT} with {len(guides)} guides",
            "the sweep takes one guide, which it uses to place and scale each section. "
            "Two guides is a different construction — build it with one guide and add "
            "intermediate sections where the second one mattered",
        )

    wires = [
        _wire_of(
            named_geometry(document, reference, tool=LOFT, argument="sections"),
            reference=reference,
            tool=LOFT,
        )
        for reference in sections
    ]

    if spine:
        return _record(
            context,
            document,
            arguments,
            LOFT,
            _swept_loft(document, wires, spine, guides, closed=bool(arguments.get("closed"))),
            SURFACE,
            "loft",
        )

    maker = symbol("BRepOffsetAPI_ThruSections")(False, False)
    if arguments.get("closed"):
        maker.SetContinuity(symbol("GeomAbs_Shape").GeomAbs_C0)
    for wire in wires:
        maker.AddWire(wire)

    lofted = build_or_raise(
        maker,
        tool=LOFT,
        detail="The sections could not be lofted through. They must each be a single "
        "closed or open wire, and they must not cross one another.",
    )
    return _record(context, document, arguments, LOFT, lofted, SURFACE, "loft")


def _swept_loft(
    document: Any,
    wires: Sequence[Any],
    spine: Any,
    guides: Sequence[Any],
    *,
    closed: bool,
) -> Any:
    """Sections carried along a spine, optionally kept in contact with a guide.

    `ContactOnBorder` rather than `NoContact` is what makes a guide mean anything: without
    it the guide only turns the section about the spine, which for a circular section
    changes nothing at all — the surface comes back byte-identical to the unguided sweep
    and reports success. With it, the section is scaled to touch the guide, which is what
    a guide curve is for.
    """
    if closed:
        raise OperationNotSupported(
            f"{LOFT} closed along a spine",
            "closing a loft back onto its first section is a property of the section "
            "loft; along a spine the sweep starts and ends where the spine does. Close "
            "the spine itself, or drop the spine",
        )

    maker = symbol("BRepOffsetAPI_MakePipeShell")(
        _wire_of(
            named_geometry(document, spine, tool=LOFT, argument="spine"),
            reference=spine,
            tool=LOFT,
        )
    )
    for guide in guides:
        maker.SetMode(
            _wire_of(
                named_geometry(document, guide, tool=LOFT, argument="guides"),
                reference=guide,
                tool=LOFT,
            ),
            True,
            symbol("BRepFill_TypeOfContact").BRepFill_ContactOnBorder,
        )
    for wire in wires:
        maker.Add(wire, False, False)

    if not maker.IsReady():
        raise GeometryError(
            f"{LOFT} could not set up the sweep along {spine!r}. The spine must be one "
            "connected curve, and each section must be a wire."
        )
    return build_or_raise(
        maker,
        tool=LOFT,
        detail="The sections could not be swept along that spine. A section far from the "
        "spine, or a guide the sections cannot reach, is the usual cause.",
    )


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
        named_geometry(document, reference, tool=JOIN, argument="elements")
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
    if propagation not in _PROPAGATION_MODES:
        raise GeometryError(
            f"{EXTRACT} takes propagation of {', '.join(sorted(_PROPAGATION_MODES))}; got "
            f"{arguments.get('propagation')!r}."
        )

    source = context.require_shape(EXTRACT)
    wanted: list[Any] = []
    kind = SURFACE
    for reference in references:
        found = named_geometry(document, reference, tool=EXTRACT, argument="elements")
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

    if propagation != "none":
        if kind == CURVE:
            raise OperationNotSupported(
                f"{EXTRACT} with propagation over edges",
                "propagation spreads across faces, over the edges they share. An edge's "
                "own chain is a different walk — take the boundary you want by selector",
            )
        wanted = _propagated_faces(
            source,
            [face for piece in wanted for face in faces(piece)],
            propagation,
            tool=EXTRACT,
        )

    taken = wanted[0] if len(wanted) == 1 else compound(wanted)
    return _record(context, document, arguments, EXTRACT, taken, kind, "extract")


def _propagated_faces(
    source: Any, seeds: Sequence[Any], mode: str, *, tool: str
) -> list[Any]:
    """Every face reachable from the seeds, by the continuity asked for.

    `point_continuity` crosses any shared edge, so it takes the whole connected skin.
    `tangent_continuity` crosses only where the two faces meet smoothly, which is what
    picks up a fillet and its neighbours and stops at the first sharp corner — the reason
    to propagate at all, since "the rounded end of this part" is a set of faces nobody
    wants to enumerate.

    **Tangency is measured at the shared edge, not between the faces' own normals.** A
    fillet's normal at its parametric centre is 45° from the flat face it runs into; the
    two agree only *along the edge they share*. `classify.edge_is_convex` answers a
    different question with the centre normals and would call every fillet a sharp corner
    here.
    """
    from app.kernel.occt import classify

    mapping = classify.faces_by_edge(source)
    found = list(seeds)
    frontier = list(seeds)
    while frontier:
        face = frontier.pop()
        for edge in edges(face):
            for neighbour in classify.adjoining_faces(mapping, edge):
                if any(neighbour.IsSame(known) for known in found):
                    continue
                if mode == "tangent_continuity" and not _joins_smoothly(edge, face, neighbour):
                    continue
                found.append(neighbour)
                frontier.append(neighbour)
    return found


def _joins_smoothly(edge: Any, first: Any, second: Any) -> bool:
    """Whether two faces meet without a crease along the edge they share.

    Sampled along the edge at `_JOIN_SAMPLES` points — a hedge for freeform faces, and one
    the suite does not pin; see that constant. The absolute dot product is taken for the
    reason `_worst_tangency_error` gives: on a shell whose faces are not consistently
    oriented, two normals 180° apart describe the same tangent plane.
    """
    from app.kernel.occt.operations.curves import closest_face_normal

    adaptor = symbol("BRepAdaptor_Curve")(edge)
    low, high = adaptor.FirstParameter(), adaptor.LastParameter()
    for step in range(1, _JOIN_SAMPLES + 1):
        place = adaptor.Value(low + (high - low) * step / (_JOIN_SAMPLES + 1))
        at = (place.X(), place.Y(), place.Z())
        try:
            _, _, here = closest_face_normal(first, at, tool=EXTRACT)
            _, _, there = closest_face_normal(second, at, tool=EXTRACT)
        except GeometryError:  # pragma: no cover - a degenerate face at the edge
            return False
        aligned = abs(sum(a * b for a, b in zip(here, there, strict=True)))
        if math.degrees(math.acos(max(-1.0, min(1.0, aligned)))) > _TANGENT_JOIN_TOLERANCE_DEG:
            return False
    return True


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


# -- cutting surfaces against each other --------------------------------------


def split(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Cut a surface or curve with another element and keep one side.

    `keep` chooses the side, by the rule written at the top of this module: `first` is
    the side the cutter's normal points *away* from, `second` the side it points at, and
    `both` keeps everything the cut produced. If the wrong half survives, flip it rather
    than rebuilding the cut — which is the registry's own advice and is only followable
    because the rule is fixed rather than depending on how OCCT listed the pieces.
    """
    document = context.require_document()
    element = named_geometry(document, arguments.get("element"), tool=SPLIT, argument="element")
    cutter = named_geometry(document, arguments.get("cutting"), tool=SPLIT, argument="cutting")

    keep = str(arguments.get("keep") or "first").lower()
    if keep not in {"first", "second", "both"}:
        raise GeometryError(
            f"{SPLIT} takes keep as 'first', 'second' or 'both'; got {keep!r}."
        )

    whole, cells = _split_cells(element, cutter, tool=SPLIT, named=arguments.get("element"))
    if keep == "both":
        # The split result whole, not its cells re-compounded: a cut shell is still one
        # shell, and taking its faces out and putting them back in a compound would throw
        # away exactly the connectedness `catia_close_surface` later asks about.
        result = whole
    else:
        result = _side_of(cells, cutter, keep, tool=SPLIT)
    return _record(context, document, arguments, SPLIT, result, _kind_of(result), "split")


def trim(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Cut two elements against each other and keep a chosen part of both, joined.

    Split discards one side of *one* element; trim keeps a chosen part of both and sews
    them, which is what turns two intersecting skins into one continuous one.

    `keep_first` and `keep_second` default true and mean the same thing `keep='first'`
    means in `catia_split`: the piece on the side the *other* element's normal points
    away from. Two flags rather than one because the interesting trims are the ones where
    the two answers differ — an L of two panels keeps the near side of one and the far
    side of the other.
    """
    document = context.require_document()
    references = _references(arguments.get("elements"), tool=TRIM, argument="elements", minimum=2)
    if len(references) != 2:
        raise GeometryError(
            f"{TRIM} cuts exactly two elements against each other and was given "
            f"{len(references)}. Trim them a pair at a time."
        )

    first, second = (
        named_geometry(document, reference, tool=TRIM, argument="elements")
        for reference in references
    )
    halves = []
    for target, cutter, flag, named in (
        (first, second, arguments.get("keep_first"), references[0]),
        (second, first, arguments.get("keep_second"), references[1]),
    ):
        _, cells = _split_cells(target, cutter, tool=TRIM, named=named)
        side = "first" if flag is not False else "second"
        halves.append(_side_of(cells, cutter, side, tool=TRIM))

    sewing = symbol("BRepBuilderAPI_Sewing")(DEFAULT_SEWING_TOLERANCE_MM)
    for half in halves:
        sewing.Add(half)
    sewing.Perform()
    trimmed = sewing.SewedShape()
    return _record(context, document, arguments, TRIM, trimmed, _kind_of(trimmed), "trim")


def untrim(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Restore a trimmed surface to the full extent of the surface underneath it.

    Useful when a surface was cut too small earlier and the construction that made it is
    no longer available to change.

    **An unbounded surface is refused rather than untrimmed**, and this is the whole
    difficulty of the operation. A plane's natural parameter range is infinite, and OCCT
    will happily build the face anyway: `MakeFace` on a `Geom_Plane` reports success and
    returns a face of area 8 × 10¹⁰⁰. That number then flows into a mass, a bounding box
    and any assertion reading them, all of which look like measurements. So the parameter
    bounds are tested against OCCT's own infinity marker before anything is built.
    """
    document = context.require_document()
    source = _surface_named(document, arguments.get("surface"), tool=UNTRIM, argument="surface")

    restored = []
    infinite = symbol("Precision").Infinite_s()
    for face in faces(source):
        surface = symbol("BRep_Tool").Surface_s(face)
        u_min, u_max, v_min, v_max = surface.Bounds()
        unbounded = [
            axis
            for axis, low, high in (("u", u_min, u_max), ("v", v_min, v_max))
            if abs(low) >= infinite or abs(high) >= infinite
        ]
        if unbounded:
            raise GeometryError(
                f"{UNTRIM} cannot restore {arguments.get('surface')!r}: the surface under "
                f"it runs to infinity along {' and '.join(unbounded)}, so it has no full "
                "extent to restore — a plane and a cylinder are both endless. Untrim "
                "applies to a surface with its own limits, such as a loft or a fill."
            )
        maker = symbol("BRepBuilderAPI_MakeFace")(surface, DEFAULT_SEWING_TOLERANCE_MM)
        restored.append(
            build_or_raise(
                maker,
                tool=UNTRIM,
                detail="The underlying surface is bounded but could not be rebuilt as a "
                "face.",
            )
        )

    whole = restored[0] if len(restored) == 1 else compound(restored)
    return _record(context, document, arguments, UNTRIM, whole, SURFACE, "untrim")


def disassemble(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Break a multi-cell surface or curve into its separate pieces — the inverse of join.

    Needed when one element of a joined skin has to be treated differently from the rest.

    The pieces are named `<element>.1`, `<element>.2`, … because the registry gives this
    operation no `name` argument: it produces several elements and there is nowhere to
    put several names. Numbering from the original follows CATIA and keeps the connection
    to where they came from, which a fresh anonymous name would lose.
    """
    document = context.require_document()
    reference = str(arguments.get("element") or "").strip()
    source = named_geometry(document, reference, tool=DISASSEMBLE, argument="element")

    mode = str(arguments.get("mode") or "domains").lower()
    if mode not in {"domains", "all_cells"}:
        raise GeometryError(
            f"{DISASSEMBLE} takes mode as 'domains' or 'all_cells'; got {mode!r}."
        )

    kind = _kind_of(source)
    if mode == "domains":
        parts = domains(source)
    else:
        parts = edges(source) if kind == CURVE else faces(source)

    if len(parts) < 2:
        raise GeometryError(
            f"{DISASSEMBLE} found {reference!r} to be a single piece already, so there is "
            "nothing to break up. Use mode='all_cells' to separate every face or edge of "
            "it."
        )

    made = []
    for index, part in enumerate(parts, start=1):
        name = f"{reference}.{index}"
        feature = document.add_feature(name, DISASSEMBLE)
        document.set_construction(feature, part, name=name, kind=kind)
        record_primitive(feature.labels, part)
        made.append(name)

    payload = dict(context.result_for(document.feature(made[-1])))
    payload["disassembled"] = made
    payload["mode"] = mode
    return payload


def healing(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Close small gaps between surfaces that should be continuous.

    The standard first move on imported geometry, where surfaces that met exactly in the
    source CAD arrive fractions of a millimetre apart and every downstream operation
    refuses them.

    **This is `catia_join` with a gap it expects, not a different algorithm**, and the
    difference is entirely in the default: join's tolerance is tight so a real gap is
    reported, and healing's is stated by the caller because closing one is the point. A
    heal that silently used join's tolerance would report success and change nothing.
    """
    document = context.require_document()
    references = _references(arguments.get("elements"), tool=HEALING, argument="elements", minimum=1)

    distance = arguments.get("merging_distance_mm")
    if distance is None:
        raise GeometryError(
            f"{HEALING} needs merging_distance_mm — the largest gap to close. Without it "
            "there is nothing to say how far apart two surfaces may be and still be the "
            "same edge, and guessing that is how a heal welds two walls that were meant "
            "to be apart."
        )
    merging = as_positive_length(distance, argument="merging_distance_mm", tool=HEALING)

    for unsupported, reason in (
        (
            "tangency_angle_deg",
            "smoothing a kink between two surfaces changes their shape, where healing "
            "only closes the gap between them. Offset or rebuild the surfaces that meet "
            "at the wrong angle",
        ),
        (
            "continuity",
            "point continuity is what sewing produces. Tangent continuity would have to "
            "move the surfaces, which healing does not do",
        ),
    ):
        value = arguments.get(unsupported)
        if value and not (unsupported == "continuity" and str(value).lower() == "point"):
            raise OperationNotSupported(f"{HEALING} with {unsupported}={value!r}", reason)

    sewing = symbol("BRepBuilderAPI_Sewing")(merging)
    for reference in references:
        sewing.Add(named_geometry(document, reference, tool=HEALING, argument="elements"))
    sewing.Perform()

    fixer = symbol("ShapeFix_Shape")(sewing.SewedShape())
    fixer.Perform()
    healed = fixer.Shape()
    if healed is None or healed.IsNull():
        raise GeometryError(
            f"{HEALING} produced nothing from {len(references)} elements at "
            f"{merging:g} mm."
        )

    payload = dict(_record(context, document, arguments, HEALING, healed, SURFACE, "healing"))
    payload["pieces"] = connected_pieces(healed)
    payload["merging_distance_mm"] = merging
    return payload


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


def sew_surface(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Trim a solid to a surface — how a shaped face is impressed on a prismatic part.

    The third crossing from skin into material, and the one that does not build anything
    new: `catia_close_surface` fills a closed skin, `catia_thick_surface` gives a skin
    depth, and this cuts an existing part back to a skin that crosses it.

    **Which side survives is stated**, by the same rule `catia_split` uses and for the same
    reason: the material on the side the surface's normal points *away* from is kept.
    `reversed` flips that, and so does `remove` — with a surface that crosses the part,
    adding material on one side and removing it from the other are the same cut described
    from the two ends, and CATIA offers both words for it.

    **A surface that does not cross the part is refused.** That is the case where CATIA
    would *add* material, filling the region between the surface and the part's own face,
    and it needs that face to close the region — a different construction from this one.
    Building nothing and reporting success would be the worse answer.
    """
    from app.kernel.occt.operations.features import combine_into_part

    document = context.require_document()
    body = context.require_shape(SEW)
    named = arguments.get("surface")
    surface = _surface_named(document, named, tool=SEW, argument="surface")

    # Asked before the split, not after: the splitter's own refusal for a cut that
    # changed nothing is accurate but generic, and the case worth naming here is the one
    # CATIA would answer by *adding* material.
    measure = symbol("BRepExtrema_DistShapeShape")(body, surface)
    measure.Perform()
    if measure.IsDone() and measure.Value() > DEFAULT_SEWING_TOLERANCE_MM:
        raise GeometryError(
            f"{SEW} was given {named!r}, which stands {measure.Value():.4g} mm clear of "
            "the part and so cuts nothing. Sewing a surface that misses the material "
            "would mean *adding* the region between the two, and that region needs the "
            "part's own face to close it — extend the surface until it crosses, or build "
            "the region as a skin and close it with catia_close_surface."
        )

    _, cells = _split_cells(body, surface, tool=SEW, named=named)

    # Two flips compose, exactly as `catia_plane_offset`'s signed distance and `reversed`
    # do: a design that says `remove` and one that says `reversed` mean the same cut, and
    # one that says both means neither.
    discard = "second"
    if bool(arguments.get("reversed")) != bool(arguments.get("remove")):
        discard = "first"

    removed = _side_of(cells, surface, discard, tool=SEW)
    return combine_into_part(context, document, arguments, SEW, removed, adds_material=False)


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


# -- reading a surface --------------------------------------------------------


def surface_analysis(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Curvature, draft, continuity or the gaps between named surfaces.

    Three of the four kinds are `catia_analysis_part` pointed at a surface instead of at
    the body, and they are delegated rather than reimplemented — the scans already carry
    their own provenance, and a second copy would be the one that drifted.

    `connect` is the kind with no existing answer and the one the registry's own summary
    says to run before trusting a joined skin: it sews the named elements at a tight
    tolerance and reports how many pieces that leaves and **the smallest tolerance that
    would join them**. That number is the argument `catia_healing` needs, so the analysis
    hands the repair its own parameter rather than saying "there is a gap".
    """
    from app.kernel.occt import interrogate
    from app.kernel.occt.operations.inspection import InterrogationPayload

    document = context.require_document()
    references = _references(
        arguments.get("elements"), tool=ANALYSIS, argument="elements", minimum=1
    )
    pieces = [
        named_geometry(document, reference, tool=ANALYSIS, argument="elements")
        for reference in references
    ]
    subject = pieces[0] if len(pieces) == 1 else compound(pieces)

    kind = str(arguments.get("kind") or "").strip().lower()
    payload = InterrogationPayload()

    if kind == "curvature":
        payload.add(interrogate.scan_curvature(subject))
    elif kind == "continuity":
        payload.add(interrogate.scan_continuity(subject))
    elif kind == "draft":
        pull = as_direction(arguments.get("direction"), argument="direction")
        payload.add(interrogate.analyse_draft(subject, pull, required_deg=0.0))
    elif kind == "connect":
        payload.merge(_connection_report(pieces, arguments))
    elif kind in {"reflection", "isophote"}:
        raise OperationNotSupported(
            f"{ANALYSIS} of kind {kind!r}",
            "reflection lines and isophotes are what a rendered highlight looks like on "
            "the surface, so they are a picture rather than a number and belong with the "
            "viewer (Phase E4). 'curvature' and 'continuity' answer the same question "
            "numerically",
        )
    else:
        raise OperationNotSupported(
            f"{ANALYSIS} of kind {kind!r}",
            "supported kinds are curvature, continuity, draft and connect",
        )

    result = payload.as_dict()
    result["analysis_kind"] = kind
    result["elements"] = list(references)
    return result


def _connection_report(pieces: Sequence[Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """How far the named surfaces are from being one, in millimetres.

    Reported as *the tolerance that would join them* rather than as "there is a gap",
    because that is the number the repair takes: `catia_healing(merging_distance_mm=…)`.
    An analysis that stops at "not connected" leaves the engineer to find the figure by
    doubling it until something works.
    """
    tolerance = float(arguments.get("tolerance_mm") or DEFAULT_SEWING_TOLERANCE_MM)

    sewing = symbol("BRepBuilderAPI_Sewing")(tolerance)
    for piece in pieces:
        sewing.Add(piece)
    sewing.Perform()
    joined = sewing.SewedShape()

    report: dict[str, Any] = {
        "pieces": connected_pieces(joined),
        "tolerance_mm": tolerance,
        "free_edge_count": len(_free_edges(joined)),
    }
    if report["pieces"] > 1:
        report["gap_to_close_mm"] = _widest_gap(domains(joined))
    return report


def _free_edges(shape: Any) -> list[Any]:
    """Edges bounded by fewer than two faces — where a skin is still open."""
    analyser = symbol("ShapeAnalysis_FreeBounds")(shape)
    return explore(analyser.GetClosedWires(), EDGE) + explore(analyser.GetOpenWires(), EDGE)


def _widest_gap(parts: Sequence[Any]) -> float:
    """The smallest tolerance that would join every piece into one.

    Each piece's distance to its nearest neighbour is the tolerance that would attach
    *that* piece; the largest of those is the one that attaches them all. Taking the
    global minimum instead would report a number that joins two of five pieces and leave
    the caller believing the skin was closed.
    """
    widest = 0.0
    for index, part in enumerate(parts):
        others = [other for position, other in enumerate(parts) if position != index]
        nearest = min(_distance(part, other) for other in others)
        widest = max(widest, nearest)
    return widest


def _distance(first: Any, second: Any) -> float:
    """Exact minimum distance, and `inf` when the search has no answer to give.

    Infinity rather than zero on failure, for the reason `sweeps._distance` gives: every
    caller reads a small distance as "these nearly touch", so a failed search reported as
    zero would claim a gap that could be closed by doing nothing.
    """
    extrema = symbol("BRepExtrema_DistShapeShape")(first, second)
    if not extrema.IsDone() or extrema.NbSolution() <= 0:  # pragma: no cover - defensive
        return math.inf
    return float(extrema.Value())


# -- cutting ------------------------------------------------------------------


def _split_cells(target: Any, cutter: Any, *, tool: str, named: Any) -> tuple[Any, list[Any]]:
    """`target` cut by `cutter`: the whole result, and the cells the cut made.

    `BRepAlgoAPI_Splitter` rather than a boolean: a cut removes one side, and both sides
    are wanted here — which one survives is the caller's choice and cannot be made before
    the pieces exist.

    **The cells are sub-shapes, not connected components**, and that distinction cost a
    debugging session. Splitting a *shell* leaves the two halves sharing the cut edge, so
    they are still one connected piece and `domains` correctly returns 1 — while the
    thing the caller wants to choose between is plainly there as two faces. So the cells
    are the faces of a surface, the solids of a solid and the edges of a curve: the units
    a cut actually produces.
    """
    splitter = symbol("BRepAlgoAPI_Splitter")()
    arguments_list, tools_list = symbol("TopTools_ListOfShape")(), symbol("TopTools_ListOfShape")()
    arguments_list.Append(target)
    tools_list.Append(cutter)
    splitter.SetArguments(arguments_list)
    splitter.SetTools(tools_list)

    result = build_or_raise(
        splitter,
        tool=tool,
        detail="The two elements could not be cut against each other. Check that they "
        "intersect, and that the cutter reaches all the way across.",
    )

    if has_solid(target):
        cells = [symbol("TopoDS").Solid_s(cell) for cell in explore(result, SOLID)]
    elif count(target, FACE):
        cells = faces(result)
    else:
        cells = edges(result)

    if len(cells) < 2:
        raise GeometryError(
            f"{tool} left {named!r} in one piece: the cutting element does not cross it. "
            "Check that the two elements actually intersect, and that the cutter reaches "
            "all the way across."
        )
    return result, cells


def _side_of(cells: Sequence[Any], cutter: Any, side: str, *, tool: str) -> Any:
    """Every cell on one side of the cutting plane, by the rule this module states.

    Sides are decided by the signed distance of each cell's centre from the cutter's
    plane, so `first` and `second` mean the same thing every time this runs. Reading them
    off OCCT's own ordering would be right about half the time and would change silently
    when the geometry moved.

    **Every** cell on that side, not the furthest one: a surface that crosses the plane
    twice is cut into three, and "the first side" then means two of them. Taking one
    would silently drop material the caller asked to keep.
    """
    frame = _cutting_plane(cutter)
    if frame is None:
        raise GeometryError(
            f"{tool} was asked to keep the {side} side of a cut made by an element with "
            "no single plane, so there is no side to name. Pass keep: 'both' and pick "
            "the piece you want with catia_disassemble, or cut with a planar element."
        )

    origin, normal = frame
    wanted = "first" if side == "first" else "second"
    chosen = [
        cell
        for cell in cells
        if (_signed_offset(cell, origin, normal) < 0.0) == (wanted == "first")
    ]
    if not chosen:
        raise GeometryError(
            f"{tool} found nothing on the {side} side of the cut — every piece fell on "
            "the other side. Use the other side, or check which way the cutting element "
            "faces."
        )
    return chosen[0] if len(chosen) == 1 else compound(chosen)


def _cutting_plane(cutter: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """The cutter's own plane, when every face of it lies on one — otherwise None.

    Every face, not the first: a cutter made of two planes at an angle has no side to
    speak of, and picking the first one would answer confidently about half a question.
    """
    from app.kernel.occt import classify

    found = faces(cutter)
    if not found:
        return None

    origin, normal = None, None
    for face in found:
        if classify.face_surface_type(face) != "Plane":
            return None
        this_normal = classify.face_normal(face)
        if this_normal is None:  # pragma: no cover - a planar face always has one
            return None
        if normal is None:
            origin, normal = face_centre(face), this_normal
            continue
        aligned = abs(sum(a * b for a, b in zip(normal, this_normal, strict=True)))
        if abs(aligned - 1.0) > _COPLANAR_TOLERANCE:
            return None

    return (origin, normal) if origin is not None and normal is not None else None


def _signed_offset(
    piece: Any, origin: tuple[float, float, float], normal: tuple[float, float, float]
) -> float:
    """How far a piece's centre of mass sits along the cutter's normal from its plane."""
    from app.kernel.occt import metrology

    centre = metrology.centre_of_mass_mm(piece)
    if not any(centre):
        # A shape with no volume returns a zero centroid from the volume integral, so the
        # area one is the right question for a skin. Both are exact; only the second is
        # defined here.
        centre = _area_centre(piece)
    return sum((c - o) * n for c, o, n in zip(centre, origin, normal, strict=True))


def _area_centre(shape: Any) -> tuple[float, float, float]:
    properties = symbol("GProp_GProps")()
    symbol("BRepGProp").SurfaceProperties_s(shape, properties)
    point = properties.CentreOfMass()
    return (point.X(), point.Y(), point.Z())


def _kind_of(shape: Any) -> str:
    """`surface` when the shape has a face, `curve` when it is only edges."""
    return SURFACE if count(shape, FACE) else CURVE


#: How far two face normals may drift and still count as the same plane. A dot-product
#: threshold on unit vectors, so about 0.008°.
_COPLANAR_TOLERANCE: Final = 1e-8


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


def named_geometry(document: Any, reference: Any, *, tool: str, argument: str) -> Any:
    """One named element as a shape: a constructed surface or curve, a sketch, or a selector.

    Public because `operations/curves.py` resolves exactly the same four namespaces and
    must agree with this on every one of them — including which collisions are refused.
    A second resolver would drift, and the symptom would be a name that means one thing
    to a section and another to a split.

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
    shape = named_geometry(document, reference, tool=tool, argument=argument)
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
    shape = named_geometry(document, reference, tool=tool, argument=argument)
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

    **Loose edges are chained rather than refused.** A sketch arrives holding a wire, but
    every wireframe curve — a circle from `catia_curve_circle`, a helix, a spline — is a
    bare edge with no wire around it, and refusing those made the loft usable only from
    sketches. Anything that does chain into one curve is one curve.
    """
    from app.kernel.occt.topology import WIRE

    wires = explore(shape, WIRE)
    if len(wires) == 1:
        return symbol("TopoDS").Wire_s(wires[0])
    if not wires:
        loose = edges(shape)
        if loose:
            maker = symbol("BRepBuilderAPI_MakeWire")()
            for edge in loose:
                maker.Add(edge)
            build_or_raise(
                maker,
                tool=tool,
                detail=f"{reference!r} is {len(loose)} edges that do not chain into one "
                "curve. Each section must be a single connected curve.",
            )
            return maker.Wire()
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
    "ANALYSIS",
    "BOUNDARY",
    "CLOSE",
    "SEW",
    "SEW",
    "CURVE",
    "DEFAULT_SEWING_TOLERANCE_MM",
    "DISASSEMBLE",
    "EXTRACT",
    "EXTRUDE",
    "FILL",
    "HEALING",
    "JOIN",
    "LOFT",
    "MIN_OFFSET_MM",
    "OFFSET",
    "REVOLVE",
    "SPLIT",
    "SURFACE",
    "THICKEN",
    "TRIM",
    "UNTRIM",
    "boundary",
    "close_surface",
    "sew_surface",
    "disassemble",
    "extract",
    "healing",
    "join",
    "named_geometry",
    "split",
    "surface_analysis",
    "surface_extrude",
    "surface_fill",
    "surface_loft",
    "surface_offset",
    "surface_revolve",
    "thick_surface",
    "trim",
    "untrim",
]
