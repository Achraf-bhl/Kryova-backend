"""What changed between two versions of a part, under a tolerance policy (E15 task 3).

**Line diffs on CAD are meaningless**, which the phase says outright, and the
demonstration is one line long: re-export the same part from the same kernel and
the STEP file differs — timestamps, entity numbering, floating-point tails — while
the geometry is identical to the last bit. A reviewer shown that diff learns
nothing and, worse, learns to ignore diffs.

So this compares what the geometry *is*, not what the file says: volume, surface
area, bounding box, centre of mass, the counts of solids, faces, edges and
vertices, and the checksum. Two versions with the same checksum are the same
bytes and the comparison stops there.

**The tolerance policy is the load-bearing part, and it is relative.** A 0.1 mm³
change is nothing on a gearbox casing and is the whole part on an O-ring groove,
so an absolute tolerance is wrong at one end of the range or the other and there
is no single number that is right at both. Every continuous quantity is compared
as a *fraction* of the larger of the two values, against `RELATIVE_TOLERANCE`,
with an absolute floor for the case a relative test cannot handle: a value that
went to or from zero.

**Counts are exact and are never tolerated.** A part that gained a face gained a
face. There is no sense in which 41 faces is 40 faces to within a tolerance, and
a policy that smoothed that over would hide the single most reviewable kind of
change there is.

**"No consequential change" is a positive answer, not an empty one.** `Comparison`
is falsey when nothing moved beyond tolerance, and `summary()` says so in words.
A reviewer told "no differences" learns that the re-export was clean; a reviewer
shown a blank panel assumes it is broken.

Pure: no session, no models, no file reading. It takes the two `stats`
dictionaries `app.geometry.inspect` already produces and which
`GeometryVersion.stats` already stores, so nothing new has to be measured and
this is testable without a kernel.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeGuard

#: How much a measured quantity may move before it is called a change. 0.1% —
#: comfortably above the noise of a re-tessellation or a re-export, and far
#: below any edit somebody made on purpose. Chosen relative rather than
#: absolute; see the module docstring for why no absolute number works.
RELATIVE_TOLERANCE: Final = 1e-3

#: The floor, in the quantity's own unit, below which a *relative* test cannot
#: be applied because the denominator has gone to zero. A part whose volume went
#: from 0 to 0.0004 mm³ has not changed by an infinite fraction; it has changed
#: by 0.0004 mm³, which is nothing. Without this a version that gained a
#: hairline sliver would report as an unbounded change.
ABSOLUTE_FLOOR: Final = 1e-9

#: The continuous quantities, with the unit each is reported in. mm-N-MPa
#: throughout, and **nothing here converts** — the units rule applies to a diff
#: as much as to a result page.
_SCALARS: Final[tuple[tuple[str, str], ...]] = (
    ("volume_mm3", "mm³"),
    ("surface_area_mm2", "mm²"),
    ("mass_kg", "kg"),
)

#: The exact quantities. Compared by equality, never by tolerance.
_COUNTS: Final[tuple[str, ...]] = (
    "solid_count",
    "shell_count",
    "face_count",
    "edge_count",
    "vertex_count",
    "triangle_count",
)

#: Vector quantities compared component-wise, each component under the same
#: relative rule. Reported as one change rather than three, because "the bounding
#: box moved" is the fact and which axis it moved on is the detail.
_VECTORS: Final[tuple[tuple[str, str], ...]] = (
    ("centre_of_mass_mm", "mm"),
    ("bounding_box_min_mm", "mm"),
    ("bounding_box_max_mm", "mm"),
)


@dataclass(frozen=True)
class Change:
    """One quantity that is not what it was.

    `relative` is `None` for a count, because a count has no meaningful
    fractional change and printing one would invite a reader to compare it
    against the tolerance it was never tested against.
    """

    name: str
    before: Any
    after: Any
    unit: str = ""
    relative: float | None = None
    exact: bool = False

    def __str__(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        head = f"{self.name}: {_render(self.before)}{unit} -> {_render(self.after)}{unit}"
        if self.relative is None:
            return head
        return f"{head} ({self.relative * 100:.3g}%)"


@dataclass(frozen=True)
class Comparison:
    """Everything consequential that differs between two versions.

    Falsey when nothing did. That is the check a reviewer's screen wants first,
    and it is also what makes a re-export cheap to accept: identical geometry
    costs one comparison rather than a page of noise somebody has to read.
    """

    identical_bytes: bool = False
    changes: tuple[Change, ...] = ()
    #: Quantities present in one version and not the other. Reported apart from
    #: `changes` because the recovery differs: a change is reviewed, a missing
    #: measurement means one of the two versions was never fully inspected and
    #: the comparison is *incomplete* rather than clean.
    unmeasured: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.changes)

    @property
    def complete(self) -> bool:
        """Was every quantity comparable on both sides?

        A comparison that is not complete must not be read as "no differences".
        `summary()` says which, and this is the flag a caller checks before
        painting anything green — the same rule Decision 3 applies to an
        unmeasured assertion.
        """
        return not self.unmeasured

    def summary(self) -> str:
        if self.identical_bytes:
            return "Identical: both versions are the same bytes."
        if not self.changes and self.complete:
            # A positive answer. A blank panel reads as broken.
            return (
                f"No consequential change: every measured quantity is within "
                f"{RELATIVE_TOLERANCE * 100:g}% of the previous version."
            )
        parts: list[str] = []
        if self.changes:
            parts.append(
                f"{len(self.changes)} change(s): " + "; ".join(str(c) for c in self.changes)
            )
        if self.unmeasured:
            parts.append(
                f"{len(self.unmeasured)} quantity/quantities could not be compared "
                f"({', '.join(self.unmeasured)}), so this comparison is incomplete — "
                "it is not evidence that nothing else moved"
            )
        return ". ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "identical_bytes": self.identical_bytes,
            "changed": bool(self.changes),
            "complete": self.complete,
            "tolerance": RELATIVE_TOLERANCE,
            "changes": [
                {
                    "name": change.name,
                    "before": change.before,
                    "after": change.after,
                    "unit": change.unit,
                    "relative": change.relative,
                    "exact": change.exact,
                }
                for change in self.changes
            ],
            "unmeasured": list(self.unmeasured),
            "summary": self.summary(),
        }


def compare(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    before_sha256: str | None = None,
    after_sha256: str | None = None,
    tolerance: float = RELATIVE_TOLERANCE,
) -> Comparison:
    """Compare two versions' measured properties under the tolerance policy.

    `before_sha256`/`after_sha256` are the content addresses. When they match,
    the two versions are the same blob and the comparison short-circuits — the
    content-addressed store already answers "is this the same file", and
    re-deriving that from measurements would be slower and less certain.
    """
    if before_sha256 and after_sha256 and before_sha256 == after_sha256:
        return Comparison(identical_bytes=True)

    changes: list[Change] = []
    unmeasured: list[str] = []

    for name in _COUNTS:
        left, right = before.get(name), after.get(name)
        if left is None and right is None:
            continue
        if left is None or right is None:
            unmeasured.append(name)
            continue
        if int(left) != int(right):
            # Exact. There is no sense in which 41 faces is 40 faces to within a
            # tolerance, and smoothing it over hides the most reviewable kind of
            # change there is.
            changes.append(Change(name=name, before=int(left), after=int(right), exact=True))

    for name, unit in _SCALARS:
        left, right = before.get(name), after.get(name)
        if left is None and right is None:
            continue
        if left is None or right is None:
            unmeasured.append(name)
            continue
        moved = _relative_change(float(left), float(right))
        if moved > tolerance:
            changes.append(
                Change(name=name, before=float(left), after=float(right), unit=unit, relative=moved)
            )

    for name, unit in _VECTORS:
        left, right = before.get(name), after.get(name)
        if left is None and right is None:
            continue
        if not _is_vector(left) or not _is_vector(right):
            unmeasured.append(name)
            continue
        before_vector = [float(item) for item in left]
        after_vector = [float(item) for item in right]
        if len(before_vector) != len(after_vector):
            # Two vectors of different length are not comparable, and pairing
            # them off would silently compare a 3-vector's x against a
            # 2-vector's x and drop the rest.
            unmeasured.append(name)
            continue
        moved = max(
            (
                _relative_change(a, b)
                for a, b in zip(before_vector, after_vector, strict=True)
            ),
            default=0.0,
        )
        if moved > tolerance:
            changes.append(
                Change(
                    name=name,
                    before=before_vector,
                    after=after_vector,
                    unit=unit,
                    relative=moved,
                )
            )

    return Comparison(changes=tuple(changes), unmeasured=tuple(unmeasured))


def _relative_change(before: float, after: float) -> float:
    """How much this moved, as a fraction of the larger magnitude.

    Against the larger of the two rather than against `before`, so the answer is
    symmetric: a part that doubled and a part that halved have moved by the same
    amount and should report the same fraction. Dividing by `before` makes the
    second one 50% and the first 100%, which would put a growing part and a
    shrinking one on different sides of the tolerance for the same edit.
    """
    difference = abs(after - before)
    if difference <= ABSOLUTE_FLOOR:
        return 0.0
    scale = max(abs(before), abs(after))
    if scale <= ABSOLUTE_FLOOR:
        # Both sides are effectively zero and yet they differ by more than the
        # floor — impossible arithmetically, but returning `inf` here rather
        # than dividing by ~0 keeps the failure legible if it ever is.
        return float("inf")
    return difference / scale


def _is_vector(value: Any) -> TypeGuard[Sequence[Any]]:
    """A non-empty sequence of numbers, and not a string.

    A `TypeGuard` rather than a bare `bool` so the narrowing reaches the caller:
    without it the checker still believes `left` may be `None` on the line after
    the guard, which is exactly the confusion the guard exists to remove.
    """
    return isinstance(value, Sequence) and not isinstance(value, str | bytes) and bool(value)


def _render(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return "[" + ", ".join(_render(item) for item in value) + "]"
    return str(value)


__all__ = [
    "ABSOLUTE_FLOOR",
    "RELATIVE_TOLERANCE",
    "Change",
    "Comparison",
    "compare",
]
