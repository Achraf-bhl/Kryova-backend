"""A tiny PNG encoder and raster canvas, with no dependencies.

`catia_capture_view` has to return a real PNG: the agent is told to look at its
own work, and the image is stored in the blob store and rendered in the browser.
Mock mode therefore has to produce a decodable image, not a stub.

Pillow would do this in three lines and is not installed -- and adding an
imaging library to a daemon that has to be installed on a locked-down
engineering workstation, purely so a *mock* can draw a box, is a poor trade.
PNG's baseline encoder is about sixty lines: an 8-bit RGB IHDR, one zlib stream
of scanlines each prefixed with filter type 0, and an IEND. That is all this is.
"""

import struct
import zlib


class Canvas:
    """An RGB raster with just enough drawing for a wireframe preview."""

    def __init__(self, width: int, height: int, background: tuple[int, int, int]) -> None:
        self.width = width
        self.height = height
        self._pixels = bytearray(bytes(background) * (width * height))

    def set(self, x: int, y: int, colour: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self._pixels[offset : offset + 3] = bytes(colour)

    def line(
        self, x0: int, y0: int, x1: int, y1: int, colour: tuple[int, int, int], width: int = 1
    ) -> None:
        """Bresenham, thickened by stamping a square nib at each step."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        span = range(-(width // 2), width // 2 + 1)
        while True:
            for ox in span:
                for oy in span:
                    self.set(x0 + ox, y0 + oy, colour)
            if x0 == x1 and y0 == y1:
                return
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x0 += sx
            if doubled <= dx:
                error += dx
                y0 += sy

    def fill_polygon(self, points: list[tuple[int, int]], colour: tuple[int, int, int]) -> None:
        """Scanline fill of a convex polygon -- enough for a projected face."""
        if len(points) < 3:
            return
        top = max(0, min(y for _, y in points))
        bottom = min(self.height - 1, max(y for _, y in points))
        for y in range(top, bottom + 1):
            crossings = []
            for index in range(len(points)):
                (ax, ay), (bx, by) = points[index], points[(index + 1) % len(points)]
                if ay == by:
                    continue
                if min(ay, by) <= y < max(ay, by):
                    crossings.append(ax + (y - ay) * (bx - ax) / (by - ay))
            crossings.sort()
            for pair in range(0, len(crossings) - 1, 2):
                for x in range(int(crossings[pair]), int(crossings[pair + 1]) + 1):
                    self.set(x, y, colour)

    def to_png(self) -> bytes:
        return encode_png(self.width, self.height, bytes(self._pixels))


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode raw 8-bit RGB rows as a PNG."""
    if len(rgb) != width * height * 3:
        raise ValueError(f"expected {width * height * 3} bytes of RGB, got {len(rgb)}")
    # Filter byte 0 ("None") in front of every scanline. Real encoders choose a
    # filter per row for compression; for a flat-shaded diagram it buys little
    # and costs the only complexity in this file.
    raw = bytearray()
    stride = width * 3
    for row in range(height):
        raw.append(0)
        raw += rgb[row * stride : (row + 1) * stride]

    header = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)  # 8-bit, truecolour
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )
