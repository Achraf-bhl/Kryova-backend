"""What visibly changed between two renders.

Phase 4.3. The master plan calls this "cheap, and startlingly effective at catching what
numeric checks miss", and the reason it works is that it answers a question the other
checks cannot ask. A mass says a number moved. A plan digest says an argument moved. A
render diff says **where on the part** something moved, which is the first question a
reviewer actually has.

Two things make it usable rather than noise, and both are decisions rather than defaults:

**The two renders must share a frame, and that is enforced rather than assumed.** Framed
independently, a part that grew by 2 mm is drawn at a slightly smaller scale and *every*
pixel changes — a diff that lights up everywhere says nothing. `render_pair` and
`render_views` exist to produce comparable images; a diff of anything else is refused.

**Added and removed are different colours, not one "changed" mask.** Knowing that a
pocket appeared is a different fact from knowing an edge vanished, and a monochrome diff
throws that away for nothing. Red is what the first render had and the second does not;
green is what the second gained.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.render import Render
from app.render.raster import BACKGROUND, to_png

#: How dark a pixel must be to count as ink rather than paper. A hidden line is grey
#: (`raster.HIDDEN`, 150) and must count, so this sits above it; nothing else is drawn
#: between there and the background.
INK_THRESHOLD: Final = 200

#: Diff colours, RGB. Deliberately the review convention rather than a designer's
#: palette: red for what went, green for what arrived, and everything unchanged dropped
#: to a pale grey so the eye reads only the change.
GONE: Final[tuple[int, int, int]] = (220, 40, 40)
ARRIVED: Final[tuple[int, int, int]] = (30, 160, 60)
UNCHANGED: Final[tuple[int, int, int]] = (205, 205, 205)
PAPER: Final[tuple[int, int, int]] = (255, 255, 255)


class FramesDiffer(ValueError):
    """The two renders were not framed the same, so a pixel diff would be meaningless."""


@dataclass(frozen=True)
class RenderDiff:
    """The visible difference between two renders of the same view.

    `fraction` is of the *inked* pixels rather than of the canvas, because the canvas is
    mostly white and a change to a small feature would otherwise read as 0.1% whether it
    mattered or not. A fraction of the ink answers "how much of the drawing is different",
    which is the question worth asking.
    """

    view: str
    gone: int
    arrived: int
    unchanged: int
    png: bytes

    @property
    def changed(self) -> int:
        return self.gone + self.arrived

    @property
    def fraction(self) -> float:
        total = self.changed + self.unchanged
        return self.changed / total if total else 0.0

    @property
    def identical(self) -> bool:
        return self.changed == 0


def diff(before: Render, after: Render) -> RenderDiff:
    """Compare two renders drawn on the same frame.

    The comparison is on **ink, not shade**: a line that was hidden and is now visible has
    changed shade but not position, and reporting that as a change would flag every part
    whose features merely reordered behind each other. Whether something is drawn at all
    is the robust question, and it is the one a reviewer means by "what changed".
    """
    if before.frame != after.frame:
        raise FramesDiffer(
            "These two renders were framed differently, so a pixel diff would show the "
            "framing rather than the change. Produce them with render_pair (two parts, "
            "one view) or render_views (one part, several views), both of which compute "
            "one frame and use it for every image."
        )
    if before.view != after.view:
        raise FramesDiffer(
            f"These renders are of different views ({before.view!r} and {after.view!r}). "
            "A diff across views compares two pictures of different things."
        )

    was = _ink(before)
    now = _ink(after)
    gone = was & ~now
    arrived = now & ~was
    both = was & now

    height, width = was.shape
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:, :] = PAPER
    canvas[both] = UNCHANGED
    canvas[gone] = GONE
    canvas[arrived] = ARRIVED

    return RenderDiff(
        view=before.view,
        gone=int(gone.sum()),
        arrived=int(arrived.sum()),
        unchanged=int(both.sum()),
        png=to_png(canvas),
    )


def _ink(shot: Render) -> NDArray[np.bool_]:
    """Which pixels of a render carry a line, recovered from the PNG it published.

    Decoded from `shot.png` rather than kept alongside it as an array, on purpose: the
    PNG is the artefact — it is what gets hashed, stored, shown to a vision model and
    attached to a review. A diff computed from a parallel copy of the pixels could differ
    from what anybody actually looked at, which is the whole failure mode this package
    is trying not to have.
    """
    return _decode(shot.png) < INK_THRESHOLD


def _decode(png: bytes) -> NDArray[np.uint8]:
    """An 8-bit greyscale PNG written by `raster.to_png`, back to an array.

    Deliberately narrow: it reads the files this package writes — filter type 0 on every
    row, one IDAT, colour type 0 — and nothing else. A general PNG decoder is a
    dependency and a surface; this is nine lines and cannot be surprised by a file it did
    not produce, because it refuses one.
    """
    import struct
    import zlib

    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG.")
    width, height, depth, colour = struct.unpack(">IIBB", png[16:26])
    if (depth, colour) != (8, 0):
        raise ValueError(
            f"This decoder reads the 8-bit greyscale PNGs app.render writes, not "
            f"depth {depth} colour type {colour}."
        )

    data = bytearray()
    at = 8
    while at < len(png):
        size = struct.unpack(">I", png[at : at + 4])[0]
        kind = png[at + 4 : at + 8]
        if kind == b"IDAT":
            data.extend(png[at + 8 : at + 8 + size])
        at += 12 + size

    raw = zlib.decompress(bytes(data))
    stride = width + 1
    rows = [raw[row * stride + 1 : (row + 1) * stride] for row in range(height)]
    if any(raw[row * stride] != 0 for row in range(height)):
        raise ValueError("This decoder reads unfiltered rows only (filter type 0).")
    return np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(height, width)


__all__ = [
    "ARRIVED",
    "BACKGROUND",
    "GONE",
    "INK_THRESHOLD",
    "PAPER",
    "UNCHANGED",
    "FramesDiffer",
    "RenderDiff",
    "diff",
]
