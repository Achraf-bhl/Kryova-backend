"""Projecting a shape to the 2D lines a view is made of.

**Hidden-line removal, not OpenGL, and that is the decision this module exists to record.**

OCCT ships a perfectly good offscreen GL renderer and OCP exposes it — `V3d`, `AIS`,
`OpenGl_GraphicDriver` all import here, and a viewer comes up on this machine. It is not
what Phase 4.1 asks for. The phase wants two renders of the same geometry to be
byte-identical, and names the leverage: a render hash that forms part of the geometry's
identity, blind where mass and plan-digest are blind. A GL image is a function of the
driver, the sampling, the display server and the machine — and this project develops on
Linux and ships on Windows, so the one comparison that matters most is exactly the one a
GL render cannot make. HLR is arithmetic. It gives the same edges on any machine, with no
display attached, and the raster step below it is integer.

The cost is honest and worth stating: this draws a **wireframe**, not a shaded solid. A
VLM reading it sees the silhouette and the edges, which is what catches the gross errors
Phase 4.2 is a filter for (mirrored, inside-out, the wrong feature, a missing pocket).
It will not catch a subtly wrong blend, and neither would a shaded render — the phase
already says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from app.kernel.occt.binding import symbol
from app.kernel.occt.topology import EDGE, explore
from app.render.views import View

#: How finely a curved edge is broken into straight segments, as a fraction of the
#: projected extent. Relative rather than absolute so a 10 mm part and a 10 m part are
#: flattened into the same number of segments and therefore render identically at their
#: own scale — which is what makes a render hash a fact about the shape rather than about
#: the units somebody typed.
DEFLECTION: Final = 4e-4

#: Segments a curved edge is never allowed to exceed, whatever the deflection asks for.
#: A spline with a cusp can otherwise ask for a practically unbounded number, and a
#: renderer that hangs on one part is worse than one that draws a slightly coarse curve.
MAX_SEGMENTS: Final = 4096


@dataclass(frozen=True)
class Projection:
    """A view's worth of 2D geometry, in view millimetres.

    `visible` and `hidden` are kept apart rather than merged with a style flag because
    they are drawn differently *and* because a render diff (4.3) that saw a line move
    from hidden to visible would otherwise report only that some pixels changed. The
    distinction is the most informative thing in the image: it is what says a feature is
    behind another one.
    """

    view: str
    visible: tuple[tuple[tuple[float, float], ...], ...]
    hidden: tuple[tuple[tuple[float, float], ...], ...]
    extent: tuple[float, float, float, float]

    @property
    def is_empty(self) -> bool:
        return not self.visible and not self.hidden


def project(shape: Any, view: View) -> Projection:
    """Every edge of `shape` as seen from `view`, split into visible and hidden.

    HLR hands its result back **already in the projection plane** — z is zero and x, y are
    the view's own coordinates — so nothing here transforms anything. That is worth
    knowing before reading this: the obvious implementation, projecting each point by
    hand against the camera basis, would be a second answer to a question OCCT has
    already answered, and the two would drift.

    **The direction handed to HLR is negated, and that is the whole of the camera.**
    `View.direction` points from the eye towards the part, which is what every other
    reader of it wants. `HLRAlgo_Projector` takes the opposite sense: the `gp_Ax2` it is
    built from has its main direction pointing from the part *towards the eye*, and that
    direction is what decides which side of the part the algorithm is standing on — which
    is to say, which lines come back visible and which come back hidden.

    Handing it the un-negated direction put the eye on the far side of the part in all
    eight canonical views. Nothing looked wrong, because an orthographic silhouette is
    unchanged by viewing the part from behind and mirroring it — so the outline, the
    framing, the extent, the render digest and every determinism and diff check were all
    exactly as they should be. Only the visible/hidden split was inverted: a pocket you
    are looking straight into came back dashed, and a pocket on the far side came back
    solid. Measured on 2026-09-05 with a blind pocket in each of the six faces in turn;
    every one of them reported 0 added visible and 8 added hidden lines in the view that
    faces it. That is the same class of error as the upside-down render this module
    already carries a note about, and it survived for the same reason.

    Negating it here also removes the y correction `_flatten` used to apply, and the two
    are one fact rather than two: `gp_Ax2` defines its Y axis as `main × X`, so with the
    main direction the right way round that Y axis *is* the up vector `views.View`
    declares (`right × direction`) instead of its opposite. The two sign errors cancelled
    in the 2D coordinates, which is why fixing only the visible one left this behind. The
    view millimetres are unchanged by this fix; the depth ordering is not.
    """
    projector = symbol("HLRAlgo_Projector")(
        symbol("gp_Ax2")(
            symbol("gp_Pnt")(0.0, 0.0, 0.0),
            symbol("gp_Dir")(*(-component for component in view.direction)),
            symbol("gp_Dir")(*view.right()),
        )
    )
    algorithm = symbol("HLRBRep_Algo")()
    algorithm.Add(shape)
    algorithm.Projector(projector)
    algorithm.Update()
    algorithm.Hide()
    drawn = symbol("HLRBRep_HLRToShape")(algorithm)

    visible = _polylines(drawn, ("VCompound", "Rg1LineVCompound", "OutLineVCompound"))
    hidden = _polylines(drawn, ("HCompound", "Rg1LineHCompound", "OutLineHCompound"))
    return Projection(
        view=view.name,
        visible=visible,
        hidden=hidden,
        extent=_extent(visible + hidden),
    )


def _polylines(
    drawn: Any, streams: tuple[str, ...]
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Flatten several HLR compounds into polylines, in a stated order.

    **Three streams per side, not one.** `VCompound` is the sharp edges — the ones that
    exist in the model — and taking only those loses every curved silhouette, so a
    cylinder seen from the side renders as nothing at all. `OutLineVCompound` is the
    silhouette and `Rg1LineVCompound` is the smooth (tangent) edge between two faces that
    meet without a crease. All three are what a drawing shows.

    The order is fixed and the streams are concatenated rather than merged, because the
    raster below is order-independent only up to overdraw — and a render hash must not
    depend on which stream OCCT happened to fill first.
    """
    found: list[tuple[tuple[float, float], ...]] = []
    for name in streams:
        getter = getattr(drawn, name, None)
        if getter is None:  # pragma: no cover - the OCP build always has these
            continue
        try:
            compound = getter()
        except Exception:  # noqa: BLE001 - HLR raises Standard_Failure on an empty stream
            continue
        if compound is None or compound.IsNull():
            continue
        for edge in explore(compound, EDGE):
            # Edge_s, not the raw TopoDS_Shape `explore` answers in: BRepAdaptor_Curve is
            # overloaded on the concrete type and refuses the base one.
            line = _flatten(symbol("TopoDS").Edge_s(edge))
            if len(line) >= 2:
                found.append(line)
    return tuple(found)


def _flatten(edge: Any) -> tuple[tuple[float, float], ...]:
    """One projected edge as a polyline, straight edges kept as two points.

    A line is not sampled: its two ends are exact, and sampling it would put a hundred
    collinear points where two would do and make every render slower for nothing. A curve
    is sampled at a segment count derived from its own length, so the same curve always
    gets the same points — `GCPnts_QuasiUniformDeflection` would give a slightly better
    distribution and does **not** guarantee the same count for the same curve under a
    different parameterisation, which is exactly the determinism this needs.
    """
    from app.kernel.occt import classify

    adaptor = symbol("BRepAdaptor_Curve")(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    if not (math.isfinite(first) and math.isfinite(last)) or last <= first:
        return ()

    if classify.edge_curve_type(edge) == "Line":
        steps = 1
    else:
        length = symbol("GCPnts_AbscissaPoint").Length_s(adaptor, first, last)
        steps = min(MAX_SEGMENTS, max(2, math.ceil(math.sqrt(length / DEFLECTION))))

    points = []
    for index in range(steps + 1):
        at = adaptor.Value(first + (last - first) * index / steps)
        # **y is taken as HLR reports it, and that depends on `project` negating
        # the direction it builds the `gp_Ax2` from.** `gp_Ax2` defines its Y
        # axis as `main x XDirection`, so with the main direction pointing from
        # the part towards the eye -- the sense HLR actually wants -- that Y axis
        # is exactly the up vector `views.py` declares (`right x direction`).
        #
        # This was a negation until 2026-09-05, correcting an upside-down image
        # that the un-negated direction caused. It worked, and it hid the other
        # half: the same wrong sense also put the eye behind the part, which a
        # vertical flip cannot fix and no test could see. Both are now one sign,
        # in `project`. Read the note there before touching either.
        points.append((at.X(), at.Y()))
    return tuple(points)


def _extent(
    lines: tuple[tuple[tuple[float, float], ...], ...],
) -> tuple[float, float, float, float]:
    """The bounding rectangle of everything drawn, in view millimetres."""
    if not lines:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [point[0] for line in lines for point in line]
    ys = [point[1] for line in lines for point in line]
    return (min(xs), min(ys), max(xs), max(ys))


__all__ = ["DEFLECTION", "MAX_SEGMENTS", "Projection", "project"]
