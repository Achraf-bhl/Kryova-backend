"""Rendering a part so that the system — and the engineer — can look at it.

Phase 4.1. One entry point, `render`, and one value, `Render`, which carries the PNG
bytes and the digest of them. The digest is the point as much as the picture is: the
master plan asks for a render hash to become part of the geometry's identity, because it
is blind where the other two checks are blind. A mass is the same mirrored; a plan digest
is the same for a part built inside-out; a render of either is not.

Read `project.py` first for the one decision that shapes everything here — hidden-line
removal rather than OpenGL, and why a GL image cannot do the job the phase asks for.

    from app.render import render, render_views

    shot = render(shape, "iso")
    shot.png            # bytes, ready to write or to hand to a vision model
    shot.digest         # sha256 of exactly those bytes

Nothing in this package touches the document, the database or a network. It takes a
`TopoDS_Shape` and returns bytes, which keeps it usable from a test, from a job, and from
the agent's tool layer without any of them knowing about the others.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.render.project import Projection, project
from app.render.raster import DEFAULT_HEIGHT, DEFAULT_WIDTH, rasterise, to_png
from app.render.views import ALL_VIEWS, ORTHOGRAPHIC, Frame, View, frame_for, view_named


@dataclass(frozen=True)
class Render:
    """One image of one part, and everything needed to say what it is a picture of.

    `frame` is carried because a diff of two renders is only meaningful when both were
    framed identically, and the caller is the only one who can know whether they were —
    see `render_pair`, which is the supported way to get two comparable images.
    """

    view: str
    png: bytes
    width: int
    height: int
    frame: Frame
    projection: Projection

    @property
    def digest(self) -> str:
        """sha256 of the PNG bytes. Stable across machines by construction."""
        return hashlib.sha256(self.png).hexdigest()

    @property
    def is_blank(self) -> bool:
        """Whether the projection found nothing to draw.

        Reported rather than raised: an empty render is a true picture of an empty part,
        and a caller rendering a construction geometry set that happens to hold no solid
        wants to be told that, not to have the call fail.
        """
        return self.projection.is_empty


def render(
    shape: Any,
    view: str | View = "iso",
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    frame: Frame | None = None,
) -> Render:
    """One canonical view of `shape`, as a PNG.

    `frame` overrides the automatic framing, which is what makes two renders comparable
    — pass the first render's frame to the second and the images differ only where the
    geometry does. Left out, the part is fitted to the canvas, which is the right default
    for looking at one part and the wrong one for comparing two.
    """
    camera = view if isinstance(view, View) else view_named(view)
    projection = project(shape, camera)
    fitted = frame or frame_for(projection.extent, width, height)
    canvas = rasterise(fitted, projection.visible, projection.hidden)
    return Render(
        view=camera.name,
        png=to_png(canvas),
        width=fitted.width,
        height=fitted.height,
        frame=fitted,
        projection=projection,
    )


def render_views(
    shape: Any,
    views: tuple[str, ...] = ORTHOGRAPHIC,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> dict[str, Render]:
    """Several views of one part, **each framed the same**.

    The frame is computed once, over the union of every view's extent, so a plate does
    not appear to grow when seen from the end. Framing each view to its own extent is the
    obvious implementation and it makes a six-view sheet unreadable: the six pictures are
    then at six different scales with nothing saying so.
    """
    camera = [view_named(name) for name in views]
    projections = [project(shape, one) for one in camera]
    union = _union(tuple(one.extent for one in projections))
    shared = frame_for(union, width, height)

    rendered: dict[str, Render] = {}
    for one, projection in zip(camera, projections, strict=True):
        canvas = rasterise(shared, projection.visible, projection.hidden)
        rendered[one.name] = Render(
            view=one.name,
            png=to_png(canvas),
            width=shared.width,
            height=shared.height,
            frame=shared,
            projection=projection,
        )
    return rendered


def render_pair(
    before: Any,
    after: Any,
    view: str | View = "iso",
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> tuple[Render, Render]:
    """Two parts through one frame, so the difference between the images is the change.

    This is what Phase 4.3's diff is built on and it is why the frame is a value at all.
    Rendering the two independently and diffing the results answers "did anything move",
    to which the answer is yes, because the second part fitted the canvas differently.
    """
    camera = view if isinstance(view, View) else view_named(view)
    projections = (project(before, camera), project(after, camera))
    shared = frame_for(_union(tuple(one.extent for one in projections)), width, height)
    return tuple(  # type: ignore[return-value]
        Render(
            view=camera.name,
            png=to_png(rasterise(shared, one.visible, one.hidden)),
            width=shared.width,
            height=shared.height,
            frame=shared,
            projection=one,
        )
        for one in projections
    )


def _union(
    extents: tuple[tuple[float, float, float, float], ...],
) -> tuple[float, float, float, float]:
    """The rectangle covering several projected extents, ignoring empty ones.

    An empty projection reports `(0, 0, 0, 0)`, which is a real point at the origin and
    would drag the union out to include it — framing a 200 mm part that happens to sit
    far from the origin at half the scale it should have. Dropped rather than special-
    cased at the call site, because every caller here would need the same guard.
    """
    real = [one for one in extents if one != (0.0, 0.0, 0.0, 0.0)]
    if not real:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(one[0] for one in real),
        min(one[1] for one in real),
        max(one[2] for one in real),
        max(one[3] for one in real),
    )


__all__ = [
    "ALL_VIEWS",
    "ORTHOGRAPHIC",
    "Frame",
    "Projection",
    "Render",
    "View",
    "render",
    "render_pair",
    "render_views",
    "view_named",
]
