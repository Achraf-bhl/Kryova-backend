"""The canonical views, and how a part is framed inside one.

Phase 4.1 asks for six orthographic views, two isometric ones and section cuts, from a
*fixed* camera — fixed because the leverage the phase is after is a render hash that forms
part of the geometry's identity, and a camera that drifts by a pixel makes that hash mean
nothing.

**The framing is derived from the part, not chosen.** A view fits the projected extent
inside the canvas with a stated margin, so the same geometry always lands on the same
pixels and two parts that differ only in size render at different scales rather than
differently. That is deliberate: a render is asked "does this look like the thing I
described", not "is this 60 mm wide" — the measurement layer answers the second, exactly,
and a render that tried to would be the worse of two answers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

#: Which way is up in the world. Everything here is Z-up and right-handed, matching the
#: kernel's own convention (`operations.context.DEFAULT_AXIS`) — a renderer with its own
#: idea of up is a renderer whose "top view" is somebody else's side.
WORLD_UP: Final[tuple[float, float, float]] = (0.0, 0.0, 1.0)

#: Fraction of the canvas left empty around the part, per side. Enough that a thick
#: outline is never clipped by the frame and a VLM sees the whole silhouette.
MARGIN: Final = 0.06


@dataclass(frozen=True)
class View:
    """One camera: where it looks from, and which way up.

    `direction` points **from the eye towards the part**, which is the sense
    `HLRAlgo_Projector` takes and the sense that makes "front" the view whose direction is
    +Y — you stand in front of the part at −Y and look at it. Getting this backwards
    renders the back of the part under the name of the front, which is the kind of error
    that survives review because the image looks perfectly good.
    """

    name: str
    direction: tuple[float, float, float]
    up: tuple[float, float, float] = WORLD_UP

    def right(self) -> tuple[float, float, float]:
        """The view's own +x axis: `direction × up`, normalised.

        Falls back to world +X when the view looks straight up or down, where `up` is
        parallel to the direction and the cross product is zero. Any choice is arbitrary
        there; making it a *stated* one keeps the top and bottom views reproducible
        instead of depending on floating-point noise in the cross product.
        """
        across = _cross(self.direction, self.up)
        if _norm(across) < 1e-12:
            across = _cross(self.direction, (0.0, 1.0, 0.0))
        return _unit(across)

    def frame_up(self) -> tuple[float, float, float]:
        """The view's own +y axis, re-orthogonalised against the direction.

        `right × direction`, which is **the opposite of OCCT's own convention**:
        a `gp_Ax2` built from the same direction and X axis has
        `YDirection = direction × X`, pointing the other way. That is not a
        detail — it is why `project.py` negates the y it gets back from HLR, and
        getting it wrong renders every part upside down while every determinism
        check still passes, because a consistently mirrored image is still
        identical to itself. See `to_view_mm`.
        """
        return _unit(_cross(self.right(), self.direction))

    def to_view_mm(self, x: float, y: float, z: float) -> tuple[float, float]:
        """One world point in view millimetres, +x right and +y up.

        The same map HLR applies, written out: the projection is linear, so a
        point's view coordinates are its components along the view's own two
        in-plane axes. This exists for the places that need an *ordered* wire —
        a section's hatch boundary — where HLR's flat pile of edges cannot say
        which point follows which. `tests/test_render.py` asserts the two agree,
        which is the guard against the second implementation drifting from the
        first.
        """
        right, up = self.right(), self.frame_up()
        return (
            x * right[0] + y * right[1] + z * right[2],
            x * up[0] + y * up[1] + z * up[2],
        )


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(v: tuple[float, float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    size = _norm(v)
    if size < 1e-12:  # pragma: no cover - guarded by the caller
        return (1.0, 0.0, 0.0)
    return (v[0] / size, v[1] / size, v[2] / size)


#: The isometric direction: equal angles to all three axes. `1/√3` each, looking down and
#: back onto the part from the front-right-top octant — the orientation every CAD package
#: calls "isometric" and the one an engineer expects to be handed.
_ISO: Final = 1.0 / math.sqrt(3.0)

CANONICAL_VIEWS: Final[dict[str, View]] = {
    "front": View("front", (0.0, 1.0, 0.0)),
    "back": View("back", (0.0, -1.0, 0.0)),
    "left": View("left", (1.0, 0.0, 0.0)),
    "right": View("right", (-1.0, 0.0, 0.0)),
    "top": View("top", (0.0, 0.0, -1.0)),
    "bottom": View("bottom", (0.0, 0.0, 1.0)),
    "iso": View("iso", (-_ISO, _ISO, -_ISO)),
    "iso_rear": View("iso_rear", (_ISO, -_ISO, -_ISO)),
}

#: The order the six orthographic views are presented in, when all of them are wanted.
ORTHOGRAPHIC: Final[tuple[str, ...]] = (
    "front",
    "back",
    "left",
    "right",
    "top",
    "bottom",
)

#: Every canonical view, orthographic first. What "render the part" means with no argument.
ALL_VIEWS: Final[tuple[str, ...]] = (*ORTHOGRAPHIC, "iso", "iso_rear")


def view_named(name: str) -> View:
    """A canonical view by name, refusing an unknown one with the list."""
    try:
        return CANONICAL_VIEWS[name.strip().lower()]
    except KeyError:
        known = ", ".join(ALL_VIEWS)
        raise ValueError(
            f"{name!r} is not a canonical view. The views are: {known}."
        ) from None


@dataclass(frozen=True)
class Frame:
    """How view millimetres map to pixels, for one render.

    Kept as a value rather than applied inline because a render diff (4.3) and a section
    cut have to place two projections on *the same* frame — a diff between two images
    framed independently shows the framing, not the change.
    """

    width: int
    height: int
    scale: float
    origin_mm: tuple[float, float]

    def to_pixels(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        """One point in view millimetres → integer pixels, y flipped.

        Rounded with `math.floor(v + 0.5)` rather than Python's `round`, which is
        banker's rounding: `round(0.5)` is 0 and `round(1.5)` is 2, so a coordinate
        landing exactly on a half-pixel snaps one way at one end of a line and the other
        way at the other. Fine for arithmetic, not for an image that has to be identical
        every time.
        """
        x = (x_mm - self.origin_mm[0]) * self.scale
        y = (y_mm - self.origin_mm[1]) * self.scale
        return (
            math.floor(x + 0.5),
            self.height - 1 - math.floor(y + 0.5),
        )


def frame_for(
    extent: tuple[float, float, float, float], width: int, height: int
) -> Frame:
    """Fit a projected extent into a canvas, centred, with `MARGIN` on every side.

    A part with no extent in one direction — a flat plate seen edge-on — still frames,
    because the scale is taken from whichever direction is *binding* and a zero span
    simply never binds. A part with no extent at all (a single point) gets a scale of 1
    rather than a division by zero, and renders as one mark in the middle, which is the
    honest picture of it.
    """
    low_x, low_y, high_x, high_y = extent
    span_x, span_y = high_x - low_x, high_y - low_y

    usable_x = width * (1.0 - 2.0 * MARGIN)
    usable_y = height * (1.0 - 2.0 * MARGIN)
    candidates = [
        usable_x / span_x if span_x > 0.0 else math.inf,
        usable_y / span_y if span_y > 0.0 else math.inf,
    ]
    scale = min(candidates)
    if not math.isfinite(scale):
        scale = 1.0

    # Centre it: the origin is the view-mm point that lands on pixel (0, height-1).
    centre_x, centre_y = (low_x + high_x) / 2.0, (low_y + high_y) / 2.0
    return Frame(
        width=width,
        height=height,
        scale=scale,
        origin_mm=(centre_x - width / (2.0 * scale), centre_y - height / (2.0 * scale)),
    )


__all__ = [
    "ALL_VIEWS",
    "CANONICAL_VIEWS",
    "MARGIN",
    "ORTHOGRAPHIC",
    "WORLD_UP",
    "Frame",
    "View",
    "frame_for",
    "view_named",
]
