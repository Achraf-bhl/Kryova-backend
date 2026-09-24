"""Whether a closed sketch outline is one simple loop.

A pad, pocket or shaft needs a profile with an inside and an outside. An outline
that crosses itself, touches itself at a vertex, or doubles back along its own
edge has neither, and the failure then arrives features later as a bare
"Update failed" -- or, worse, not at all, when the model gives up on the outline
and reaches for a simpler tool that builds a different part.

Measured 2026-09-24 on the Windows seat (qwen3.8, flanged bushing): the model
sent the closed polyline (15,0) (15,40) (25,40) (25,48) (35,48) (35,40) (25,40)
(25,0), which visits (25,40) three times. It was accepted, the model abandoned
it for `catia_sketch_revolve_profile`, which draws a uniform tube, and the turn
built a plain Ø70 tube it then spent four steps discovering.

Pure geometry, no kernel: it runs in `dispatch._augment`, before either backend,
so CATIA and the open kernel refuse the same outline in the same words.
"""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float]

#: Coordinates are millimetres. Anything closer than this is the same point.
_EPS = 1e-9


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        min(a[0], b[0]) - _EPS <= p[0] <= max(a[0], b[0]) + _EPS
        and min(a[1], b[1]) - _EPS <= p[1] <= max(a[1], b[1]) + _EPS
    )


def _meeting(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    """Where segments ab and cd meet, or None if they do not."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if abs(o1) <= _EPS and abs(o2) <= _EPS:
        for p, (s, e) in ((c, (a, b)), (d, (a, b)), (a, (c, d)), (b, (c, d))):
            if _on_segment(s, e, p):
                return p
        return None
    if (o1 > _EPS and o2 < -_EPS or o1 < -_EPS and o2 > _EPS) and (
        o3 > _EPS and o4 < -_EPS or o3 < -_EPS and o4 > _EPS
    ):
        t = o3 / (o3 - o4)
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    for p, (s, e), o in ((c, (a, b), o1), (d, (a, b), o2), (a, (c, d), o3), (b, (c, d), o4)):
        if abs(o) <= _EPS and _on_segment(s, e, p):
            return p
    return None


def _same(p: Point, q: Point) -> bool:
    return abs(p[0] - q[0]) <= _EPS and abs(p[1] - q[1]) <= _EPS


def _fmt(p: Point) -> str:
    return f"({p[0]:g}, {p[1]:g})"


def crossing(points: Sequence[Sequence[float]]) -> str | None:
    """Why this closed outline is not one simple loop, or None when it is.

    `points` are the vertices in order; the closing edge back to the first is
    implied, and a repeated first point at the end is accepted and ignored.
    """
    loop: list[Point] = [(float(p[0]), float(p[1])) for p in points]
    if len(loop) > 1 and _same(loop[0], loop[-1]):
        loop.pop()
    # Consecutive duplicates are zero-length edges, not a shape question.
    loop = [p for i, p in enumerate(loop) if i == 0 or not _same(p, loop[i - 1])]
    if len(loop) < 3:
        return None
    n = len(loop)
    edges = [(loop[i], loop[(i + 1) % n]) for i in range(n)]

    def label(i: int) -> str:
        a, b = edges[i]
        return f"edge {i + 1} {_fmt(a)} -> {_fmt(b)}"

    for i in range(n):
        for j in range(i + 1, n):
            (a, b), (c, d) = edges[i], edges[j]
            adjacent = j == i + 1 or (i == 0 and j == n - 1)
            if adjacent:
                shared, far_i, far_j = (b, a, d) if j == i + 1 else (a, b, c)
                # Adjacent edges share one vertex; they only fail by folding
                # back along each other.
                folds = (
                    abs(_orient(far_i, shared, far_j)) <= _EPS
                    and (far_i[0] - shared[0]) * (far_j[0] - shared[0])
                    + (far_i[1] - shared[1]) * (far_j[1] - shared[1])
                    > 0
                )
                if folds:
                    return (
                        f"The closed outline doubles back on itself: {label(i)} and "
                        f"{label(j)} run over each other at {_fmt(shared)}."
                        + _ADVICE
                    )
                continue
            met = _meeting(a, b, c, d)
            if met is not None:
                return (
                    f"The closed outline touches or crosses itself: {label(i)} and "
                    f"{label(j)} meet at {_fmt(met)}." + _ADVICE
                )
    return None


_ADVICE = (
    " A pad, pocket or shaft needs one simple loop that never touches itself, so "
    "it can tell inside from outside. List each corner once, in order around the "
    "outline, and let `closed` join the last corner back to the first."
)


__all__ = ["crossing"]
