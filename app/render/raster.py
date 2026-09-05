"""Turning projected lines into pixels, and pixels into a PNG.

Every step here is integer or exactly-rounded on purpose. The renderer's whole claim is
that the same geometry produces the same bytes, and the ways to lose that are all small:
anti-aliasing that depends on a float accumulation order, a dash phase that resets per
edge instead of per polyline, a PNG encoder that stamps the time into the file. Each of
those is decided explicitly below rather than inherited from a library.

**No anti-aliasing.** It would be deterministic if written carefully, and it is still not
worth it: a hard 1-bit-per-decision raster is trivially diffable (4.3 wants "what visibly
changed", and a diff of an anti-aliased image is a haze around every edge), and a vision
model reads a crisp wireframe at least as well.
"""

from __future__ import annotations

import math
import struct
import zlib
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.render.views import Frame

#: Canvas, in pixels. Large enough for a vision model to read a small feature, small
#: enough that eight views of a part are a few hundred kilobytes rather than megabytes.
DEFAULT_WIDTH: Final = 1024
DEFAULT_HEIGHT: Final = 768

#: Greys. The background is white so a render pastes into a document without inverting,
#: and a hidden line is mid-grey rather than dashed-black so that a diff can tell "this
#: line became visible" from "this line moved".
BACKGROUND: Final = 255
VISIBLE: Final = 0
HIDDEN: Final = 150

#: Line widths in pixels. A visible edge is drawn thicker than a hidden one for the same
#: reason a drawing does it: the silhouette is what the eye reads first.
VISIBLE_WIDTH: Final = 2
HIDDEN_WIDTH: Final = 1

#: Hidden lines are dashed: `DASH_ON` pixels drawn, `DASH_OFF` skipped, counted **along
#: the whole polyline** rather than restarting at each segment. Restarting per segment
#: makes the dash pattern depend on how OCCT happened to split a curve, which is exactly
#: the kind of invisible dependency that breaks a render hash.
DASH_ON: Final = 6
DASH_OFF: Final = 4

#: zlib level for the PNG. Fixed, because the *compressed* bytes are the artefact being
#: hashed and zlib's output is level-dependent. 6 is the default and a good trade; what
#: matters is that it is written down rather than left to whatever the caller's zlib
#: thinks today.
COMPRESSION: Final = 6


#: Section hatching: shade, spacing in pixels, and the 45° convention every
#: engineering drawing uses for a cut face. Lighter than a hidden line so the
#: geometry still reads over it, and spaced widely enough that a small cut face
#: gets at least a line or two rather than turning solid grey.
HATCH: Final = 190
HATCH_SPACING: Final = 9


def rasterise(
    frame: Frame,
    visible: tuple[tuple[tuple[float, float], ...], ...],
    hidden: tuple[tuple[tuple[float, float], ...], ...],
    *,
    onto: NDArray[np.uint8] | None = None,
) -> NDArray[np.uint8]:
    """Draw a projection onto a canvas, hidden lines first.

    Order matters and is stated: hidden underneath, visible on top. Where an edge is
    hidden in one place and visible in another the visible run wins the shared pixels,
    which is what a drawing does and what makes a part read correctly rather than as a
    grey smear where two features touch.

    `onto` draws over an existing canvas rather than a blank one — how a section view
    puts its outline back on top of the hatch, so a hatch line never breaks the edge it
    runs into.
    """
    canvas = (
        onto
        if onto is not None
        else np.full((frame.height, frame.width), BACKGROUND, dtype=np.uint8)
    )
    for line in hidden:
        _draw(canvas, frame, line, HIDDEN, HIDDEN_WIDTH, dashed=True)
    for line in visible:
        _draw(canvas, frame, line, VISIBLE, VISIBLE_WIDTH, dashed=False)
    return canvas


def _draw(
    canvas: NDArray[np.uint8],
    frame: Frame,
    line: tuple[tuple[float, float], ...],
    shade: int,
    width: int,
    *,
    dashed: bool,
) -> None:
    """One polyline, with the dash phase carried across its segments."""
    phase = 0
    previous = frame.to_pixels(*line[0])
    for point in line[1:]:
        current = frame.to_pixels(*point)
        phase = _segment(canvas, previous, current, shade, width, dashed, phase)
        previous = current


def _segment(
    canvas: NDArray[np.uint8],
    start: tuple[int, int],
    end: tuple[int, int],
    shade: int,
    width: int,
    dashed: bool,
    phase: int,
) -> int:
    """Bresenham from `start` to `end`, returning the dash phase it left off at.

    Integer throughout: the two endpoints were rounded once, in `Frame.to_pixels`, and
    nothing after that touches a float. That is the property the whole module is for.
    """
    x0, y0 = start
    x1, y1 = end
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    step_x = 1 if x0 < x1 else -1
    step_y = 1 if y0 < y1 else -1
    error = dx + dy

    height, canvas_width = canvas.shape
    reach = width // 2
    while True:
        if not dashed or (phase % (DASH_ON + DASH_OFF)) < DASH_ON:
            low_y, high_y = max(0, y0 - reach), min(height, y0 + reach + 1)
            low_x, high_x = max(0, x0 - reach), min(canvas_width, x0 + reach + 1)
            if low_y < high_y and low_x < high_x:
                block = canvas[low_y:high_y, low_x:high_x]
                np.minimum(block, shade, out=block)
        phase += 1

        if x0 == x1 and y0 == y1:
            return phase
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += step_x
        if doubled <= dx:
            error += dx
            y0 += step_y


def hatch(
    canvas: NDArray[np.uint8],
    frame: Frame,
    polygons: list[tuple[tuple[float, float], ...]],
) -> None:
    """Fill closed polygons with 45° hatching, by the even-odd rule.

    **Even-odd across every polygon at once, which is what makes holes free.** A
    cut face with a bore through it arrives as an outer boundary and an inner
    one; a span that crosses the inner boundary twice on its way through the
    hole flips the inside/outside parity twice and is simply not drawn. Nothing
    here has to know which wire is a hole, or that there is one.

    The hatch runs along lines of constant `x + y`, so a span is found by
    intersecting each polygon edge with that line rather than by walking
    scanlines and rotating. Integer stepping and a fixed spacing, for the same
    reason everything else in this file is integer: the picture has to come out
    the same every time.
    """
    if not polygons:
        return
    edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for polygon in polygons:
        pixels = [frame.to_pixels(*point) for point in polygon]
        if len(pixels) < 3:
            continue
        if pixels[0] != pixels[-1]:
            pixels.append(pixels[0])  # close it; a hatch of an open outline leaks
        # Consecutive pairs: head against tail. `strict` would be a bug here --
        # the tail is one shorter by construction, and the first smoke run that
        # ever reached this line found exactly that mistake.
        edges.extend(zip(pixels[:-1], pixels[1:], strict=False))
    if not edges:
        return

    keys = [x + y for edge in edges for x, y in edge]
    start = (min(keys) // HATCH_SPACING) * HATCH_SPACING
    for key in range(int(start), int(max(keys)) + 1, HATCH_SPACING):
        crossings: list[float] = []
        for (x0, y0), (x1, y1) in edges:
            # Where does this edge cross x + y = key? Parameterise along it and
            # solve; a parallel edge (constant x + y) never crosses and is
            # skipped rather than dividing by zero.
            span = (x1 + y1) - (x0 + y0)
            if span == 0:
                continue
            at = (key - (x0 + y0)) / span
            # Half-open [0, 1): a vertex shared by two edges is otherwise counted
            # twice, which flips parity back and leaves a gap through the corner.
            if 0.0 <= at < 1.0:
                crossings.append(x0 + at * (x1 - x0))
        if len(crossings) < 2:
            continue
        crossings.sort()
        for index in range(0, len(crossings) - 1, 2):
            _hatch_span(canvas, key, crossings[index], crossings[index + 1])


def _hatch_span(canvas: NDArray[np.uint8], key: int, from_x: float, to_x: float) -> None:
    """One run of a hatch line, from x to x along `x + y = key`."""
    height, width = canvas.shape
    for x in range(math.floor(from_x + 0.5), math.floor(to_x + 0.5) + 1):
        y = key - x
        if 0 <= x < width and 0 <= y < height:
            canvas[y, x] = min(int(canvas[y, x]), HATCH)


def to_png(canvas: NDArray[np.uint8]) -> bytes:
    """An 8-bit PNG, byte-for-byte reproducible. Greyscale for 2D, RGB for 3D input.

    Hand-written rather than via Pillow, and that is a real choice rather than an
    aversion to dependencies: an encoder outside this file can add an `iTXt` with a
    timestamp, change its filter heuristic between versions, or reorder ancillary chunks,
    and any of those silently breaks the one property this renderer sells. Here there are
    exactly three chunks, filter type 0 on every row, and a fixed compression level.

    PNG is a small format when nothing optional is used: a signature, IHDR, IDAT, IEND.
    """
    height, width = canvas.shape[0], canvas.shape[1]
    colour_type = 2 if canvas.ndim == 3 else 0
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # filter type 0 (None) — no heuristic, nothing to vary
        raw.extend(canvas[row].tobytes())

    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(bytes(raw), COMPRESSION)),
            _chunk(b"IEND", b""),
        )
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return b"".join(
        (
            struct.pack(">I", len(payload)),
            kind,
            payload,
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF),
        )
    )


__all__ = [
    "BACKGROUND",
    "HATCH",
    "HATCH_SPACING",
    "COMPRESSION",
    "DASH_OFF",
    "DASH_ON",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "HIDDEN",
    "HIDDEN_WIDTH",
    "VISIBLE",
    "VISIBLE_WIDTH",
    "hatch",
    "rasterise",
    "to_png",
]
