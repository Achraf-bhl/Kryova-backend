"""Does anything in the machine hit anything else — and what was actually looked at.

Master plan 14.2's packaging half, and the assembly-wide sibling of
`app.dynamics.clearance`, which asks the same question of two bodies through a motion
range. The distance query is **not reimplemented here**:
`app.kernel.interrogation.ClearanceReport` already declares the answer and
`app/kernel/occt/interrogate/proximity.py` computes it exactly with
`BRepExtrema_DistShapeShape`. This module decides *which pairs to ask about* and is
honest about the rest.

**A pairwise check is O(n^2) and this module says so before it starts.** 5,000
occurrences is 12.5 million pairs, which is not a check anybody runs. So there is a
broad phase: each occurrence's local bounding box is transformed into world space (see
`placement.Box.transformed`, conservative by construction) and a pair whose boxes are
further apart than the threshold is rejected without a narrow-phase query. Box
separation is a **lower bound** on shape separation — each shape is inside its box — so
rejecting on it can never drop a pair that would have clashed. That soundness is the
only reason the broad phase is allowed to exist.

**Everything not checked is counted and named.** This is the lesson of D-class defect
already recorded in `tests/test_dynamics_clearance.py`: a sweep that stopped early
published `measured_pose_count: 21` for nine poses it looked at, and twelve nobody had
seen were counted as measured. The same lie is available here in four flavours — a pair
rejected by bounds, a pair the author excluded, a pair the measurer could not answer,
and a pair beyond the budget — so `ClashReport` carries all four separately and
`complete` is false unless `unchecked` is empty. A clash check that silently skipped
pairs is not a weaker clash check; it is a clash check that passes everything.

**The minimum clearance over a subset is an over-estimate, and that is the dangerous
direction.** A minimum taken over fewer pairs is greater than or equal to the true
minimum, so an incomplete check makes the machine look *roomier* than it is. So
`minimum_clearance_mm` is published only when the check is complete; when it is not, the
partial number goes out as `checked_minimum_clearance_mm` and `minimum_clearance_mm`
carries an `UNAVAILABLE` provenance record naming how many pairs were not looked at. An
assertion on it then comes back `UNMEASURED`, which is the honest verdict.

**An interference that was found was found.** Unlike the minimum, an overlap is not a
bound: skipping pairs can miss a clash, it cannot invent one. So the interference volume
is reported as MEASURED with a method string that says an unchecked pair could still
clash — the same split `ClearanceSweep` keeps, deliberately worded the same way.

**Touching pairs are excluded by name, never by a magic number.** Two links on a pin
touch by design, and a bolt is inside its own clearance hole; an all-pairs check reports
both and the real finding is lost among them. `ignore` returns a *reason* rather than a
boolean, and the reasons are carried into the report — so "we did not look at these 40
pairs" is in the record with why, instead of being absent from it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.assembly.placement import Box
from app.assembly.structure import Occurrence, ProductStructure

#: Called with two occurrences and asked how close they come. Returns anything shaped
#: like `app.kernel.interrogation.ClearanceReport` — `distance_mm`, `interference_mm3`,
#: `failure`. Typed loosely for the same reason `app.dynamics.clearance.PoseMeasurer` is:
#: the caller may be driving OCCT, a CATIA seat, or a closed-form stand-in whose answer
#: is known in advance, and this module must not be able to tell which.
PairMeasurer = Callable[[Occurrence, Occurrence], Any]

#: Called with a component name and asked for its bounding box **in its own local
#: coordinates**. Local, not world: a component is measured once and placed many times,
#: which is the whole reason the structure is a graph.
BoundsProvider = Callable[[str], Box]

#: Called with two occurrences; returns a reason to skip the pair, or "" to check it.
IgnoreRule = Callable[[Occurrence, Occurrence], str]

#: How many narrow-phase queries a check will run before it stops and says so. A real
#: `BRepExtrema` query on two solids is milliseconds, so this is minutes of work, not
#: seconds — and a run that would exceed it wants to be told rather than to be waited
#: for. It is a *budget*, not a silent truncation: everything beyond it lands in
#: `ClashReport.unchecked` with the reason.
DEFAULT_PAIR_BUDGET: Final = 20_000


def touching_components(pairs: Iterable[tuple[str, str]], reason: str = "") -> IgnoreRule:
    """An `ignore` rule for component pairs that are in contact by design.

    Unordered: `("pin", "link")` also excludes `("link", "pin")`, because which one the
    walk reaches first is an accident of declaration order and an exclusion that depended
    on it would work in one assembly and not in its mirror image.
    """
    excluded = frozenset(frozenset(pair) for pair in pairs)
    text = reason or "these two components are in contact by design"

    def rule(left: Occurrence, right: Occurrence) -> str:
        if frozenset({left.component, right.component}) in excluded:
            return f"{text} ({left.component} / {right.component})"
        return ""

    return rule


def same_parent(reason: str = "") -> IgnoreRule:
    """An `ignore` rule for two occurrences under the same immediate parent.

    Offered but **not the default**, and the difference matters: the parts inside one
    sub-assembly are exactly the ones most likely to clash with each other, so excluding
    them wholesale would hide the findings worth having. It exists for the narrow case
    of a bought-in component modelled as several touching solids.
    """
    text = reason or "both occurrences are inside the same sub-assembly"

    def rule(left: Occurrence, right: Occurrence) -> str:
        return text if left.parent_path == right.parent_path else ""

    return rule


def combine(*rules: IgnoreRule) -> IgnoreRule:
    """First rule that returns a reason wins; "" when none does."""

    def rule(left: Occurrence, right: Occurrence) -> str:
        for candidate in rules:
            reason = candidate(left, right)
            if reason:
                return reason
        return ""

    return rule


@dataclass(frozen=True)
class ClashFinding:
    """One pair that was measured, and what was found.

    Every measured pair produces one of these, not only the clashing ones: the clearance
    between two parts that *nearly* touch is the finding an engineer wants before it
    becomes an interference, and a report that only listed collisions could not answer
    "how much room is there".
    """

    path_a: str
    path_b: str
    distance_mm: float | None = None
    interference_mm3: float = 0.0
    failure: str = ""

    @property
    def interferes(self) -> bool:
        return self.interference_mm3 > 0.0

    def __str__(self) -> str:
        if self.failure:
            return f"{self.path_a} vs {self.path_b}: not measured — {self.failure}"
        if self.interferes:
            return (
                f"{self.path_a} vs {self.path_b}: CLASH, "
                f"{self.interference_mm3:.4g} mm3 of overlap."
            )
        gap = "?" if self.distance_mm is None else f"{self.distance_mm:.4g}"
        return f"{self.path_a} vs {self.path_b}: {gap} mm apart."

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"a": self.path_a, "b": self.path_b}
        if self.distance_mm is not None:
            out["distance_mm"] = self.distance_mm
        if self.interference_mm3:
            out["interference_mm3"] = self.interference_mm3
        if self.failure:
            out["failure"] = self.failure
        return out


@dataclass(frozen=True)
class SkippedPair:
    """A pair that was not measured, and why. Never merely absent."""

    path_a: str
    path_b: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"a": self.path_a, "b": self.path_b, "reason": self.reason}

    def __str__(self) -> str:
        return f"{self.path_a} vs {self.path_b}: {self.reason}"


@dataclass(frozen=True)
class ClashReport:
    """What clashes, what nearly clashes, and — equally — what was never looked at.

    The four skip categories are separate because the recoveries are different.
    `rejected_by_bounds` is sound and needs nothing. `excluded` was the author's choice
    and should be reviewed. `unchecked` is work that was not done — over budget, or the
    measurer refused — and it is the one that makes `complete` false.
    """

    findings: tuple[ClashFinding, ...] = ()

    #: Every pair the occurrence set produces, before any of it is skipped.
    pairs_total: int = 0

    #: Pairs the narrow phase was actually asked about.
    narrow_checked: int = 0

    #: Pairs the bounding-box phase separated. Sound: see the module docstring.
    rejected_by_bounds: tuple[SkippedPair, ...] = ()

    #: Pairs the author's `ignore` rule excluded, each with the rule's reason.
    excluded: tuple[SkippedPair, ...] = ()

    #: Pairs nobody looked at — over budget, or the measurer failed. The honest gap.
    unchecked: tuple[SkippedPair, ...] = ()

    #: How many occurrences were walked, and the budget the run was given.
    occurrences: int = 0
    budget: int = DEFAULT_PAIR_BUDGET

    #: Pairs closer than this were reported; pairs whose *boxes* were further apart than
    #: this were rejected. Zero means "report only actual contact and overlap".
    clearance_mm: float = 0.0

    @property
    def clashes(self) -> tuple[ClashFinding, ...]:
        return tuple(f for f in self.findings if f.interferes)

    @property
    def complete(self) -> bool:
        """Was every pair either measured, soundly rejected, or deliberately excluded?"""
        return not self.unchecked

    @property
    def minimum_clearance_mm(self) -> float | None:
        """The smallest distance among the pairs that were measured.

        An **over-estimate** of the true minimum when the check is incomplete — a
        minimum over fewer pairs can only be larger. `to_payload` is where that is dealt
        with; this property is the raw number and callers reading it should read
        `complete` beside it.
        """
        measured = [f.distance_mm for f in self.findings if f.distance_mm is not None]
        return min(measured) if measured else None

    @property
    def worst_interference_mm3(self) -> float:
        return max((f.interference_mm3 for f in self.findings), default=0.0)

    @property
    def closest(self) -> ClashFinding | None:
        """The finding to look at first: a clash if there is one, else the tightest gap."""
        clashing = self.clashes
        if clashing:
            return max(clashing, key=lambda f: f.interference_mm3)
        measured = [f for f in self.findings if f.distance_mm is not None]
        if not measured:
            return None
        return min(measured, key=lambda f: f.distance_mm or 0.0)

    def to_payload(self) -> dict[str, Any]:
        """The report as a measurement payload `app.design.assertions` can read.

        Uses `app.kernel.interrogation`'s own path constants, so an assertion written
        against a two-body clearance check reads an assembly-wide one unchanged — which
        is what those constants are for.
        """
        from app.kernel import interrogation, provenance

        payload: dict[str, Any] = {
            "occurrence_count": self.occurrences,
            "pair_count": self.pairs_total,
            "checked_pair_count": self.narrow_checked,
            "rejected_by_bounds_count": len(self.rejected_by_bounds),
            "excluded_pair_count": len(self.excluded),
            "unchecked_pair_count": len(self.unchecked),
            "clash_count": len(self.clashes),
            "complete": self.complete,
        }

        minimum = self.minimum_clearance_mm
        path = interrogation.MINIMUM_CLEARANCE_MM
        if minimum is None:
            provenance.attach(
                payload,
                path,
                provenance.unavailable(
                    f"none of the {self.pairs_total} pairs returned a distance: "
                    f"{self.narrow_checked} were measured, "
                    f"{len(self.rejected_by_bounds)} were separated by their bounding "
                    f"boxes, {len(self.excluded)} were excluded and "
                    f"{len(self.unchecked)} were never looked at."
                ),
            )
        elif self.complete:
            payload[path] = minimum
            provenance.attach(
                payload,
                path,
                provenance.measured(
                    "BRepExtrema minimum-distance search over every pair not separated "
                    "by its bounding box"
                ),
            )
        else:
            # Deliberately a *different* path. The number is real but it is a minimum
            # over a subset, which over-estimates clearance — the direction that makes a
            # machine look safer than it is. Publishing it as `minimum_clearance_mm`
            # would let an assertion pass on pairs nobody looked at.
            payload["checked_minimum_clearance_mm"] = minimum
            provenance.attach(
                payload,
                path,
                provenance.unavailable(
                    f"{len(self.unchecked)} of {self.pairs_total} pairs were never "
                    "looked at, so the smallest distance found is an upper bound on the "
                    "assembly's true minimum, not the minimum. The partial number is "
                    "reported as checked_minimum_clearance_mm. "
                    + (self.unchecked[0].reason if self.unchecked else "")
                ),
            )

        payload[interrogation.INTERFERENCE_VOLUME_MM3] = self.worst_interference_mm3
        payload["interferes"] = bool(self.clashes)
        provenance.attach(
            payload,
            interrogation.INTERFERENCE_VOLUME_MM3,
            provenance.measured(
                "largest boolean-common volume over the pairs that were measured; a "
                "clash that was seen was seen, though a pair that was not looked at can "
                "still clash"
            ),
        )
        return payload

    def summary(self) -> str:
        bits = [
            f"{self.occurrences} occurrences, {self.pairs_total} pairs: "
            f"{self.narrow_checked} measured, "
            f"{len(self.rejected_by_bounds)} separated by bounds, "
            f"{len(self.excluded)} excluded"
        ]
        if self.unchecked:
            bits.append(f"{len(self.unchecked)} NEVER LOOKED AT")
        line = ", ".join(bits) + "."
        if self.clashes:
            line += f" {len(self.clashes)} clash(es)."
        closest = self.closest
        if closest is not None:
            line += f" Worst: {closest}"
        if not self.complete:
            line += (
                " This check is incomplete, so the smallest distance found is not the "
                "assembly's minimum."
            )
        return line


@dataclass
class _Tally:
    """Mutable accumulator; the frozen `ClashReport` is built from it at the end."""

    findings: list[ClashFinding] = field(default_factory=list)
    rejected: list[SkippedPair] = field(default_factory=list)
    excluded: list[SkippedPair] = field(default_factory=list)
    unchecked: list[SkippedPair] = field(default_factory=list)
    checked: int = 0
    total: int = 0


def find_clashes(
    structure: ProductStructure,
    measure: PairMeasurer,
    bounds: BoundsProvider,
    *,
    ignore: IgnoreRule | None = None,
    clearance_mm: float = 0.0,
    budget: int = DEFAULT_PAIR_BUDGET,
) -> ClashReport:
    """Check every pair of leaf occurrences, broad phase first.

    `bounds` is asked once per *component*, not once per occurrence — a bolt used forty
    times is measured once and its box transformed forty times. That is the graph
    earning its keep, and it is why `bounds` takes a component name rather than an
    occurrence.

    `clearance_mm` widens the question from "do these touch" to "is there this much room
    between them": a pair whose boxes are further apart than it is rejected, and one
    closer than it is measured and reported even if it does not clash. Zero — the default
    — reports contact and overlap only.

    Neither a measurer's exception nor its recorded failure aborts the run. Both land in
    the report: an exception as an `unchecked` pair carrying the exception text, a
    recorded `failure` as a `ClashFinding` with no distance. One pathological pair must
    not cost the answer on every other pair, which is the contract
    `proximity.measure_clearance` already keeps one layer down.
    """
    occurrences = list(structure.occurrences(leaves_only=True))
    boxes, bounds_failures = _world_boxes(occurrences, bounds)
    tally = _Tally(total=len(occurrences) * (len(occurrences) - 1) // 2)

    for index, left in enumerate(occurrences):
        for right in occurrences[index + 1 :]:
            _check_pair(
                left,
                right,
                boxes,
                bounds_failures,
                tally,
                measure,
                ignore,
                clearance_mm,
                budget,
            )

    return ClashReport(
        findings=tuple(tally.findings),
        pairs_total=tally.total,
        narrow_checked=tally.checked,
        rejected_by_bounds=tuple(tally.rejected),
        excluded=tuple(tally.excluded),
        unchecked=tuple(tally.unchecked),
        occurrences=len(occurrences),
        budget=budget,
        clearance_mm=clearance_mm,
    )


def _world_boxes(
    occurrences: Sequence[Occurrence], bounds: BoundsProvider
) -> tuple[dict[str, Box | None], dict[str, str]]:
    """A world-space box per occurrence path, and why any of them is missing.

    `bounds` is asked **once per component**; the box is then transformed once per
    occurrence. A component whose box cannot be had maps to None rather than raising:
    its pairs go into `unchecked` carrying the reason, which is a recorded gap rather
    than a run that produced nothing.

    Returned rather than kept in a module global — a global would leak one run's
    failures into the next and would not survive two checks running at once.
    """
    local: dict[str, Box | None] = {}
    failures: dict[str, str] = {}
    world: dict[str, Box | None] = {}
    for occurrence in occurrences:
        if occurrence.component not in local:
            try:
                local[occurrence.component] = bounds(occurrence.component)
            except Exception as exc:  # noqa: BLE001 - a missing box is data, not a crash
                local[occurrence.component] = None
                failures[occurrence.component] = f"{type(exc).__name__}: {exc}"
        box = local[occurrence.component]
        world[occurrence.path] = None if box is None else box.transformed(occurrence.frame)
    return world, failures


def _check_pair(
    left: Occurrence,
    right: Occurrence,
    boxes: Mapping[str, Box | None],
    bounds_failures: Mapping[str, str],
    tally: _Tally,
    measure: PairMeasurer,
    ignore: IgnoreRule | None,
    clearance_mm: float,
    budget: int,
) -> None:
    if ignore is not None:
        reason = ignore(left, right)
        if reason:
            tally.excluded.append(SkippedPair(left.path, right.path, reason))
            return

    box_a, box_b = boxes.get(left.path), boxes.get(right.path)
    if box_a is None or box_b is None:
        missing = left.component if box_a is None else right.component
        tally.unchecked.append(
            SkippedPair(
                left.path,
                right.path,
                f"no bounding box for {missing}: "
                + bounds_failures.get(missing, "the bounds provider returned nothing"),
            )
        )
        return

    if box_a.separation_mm(box_b) > clearance_mm:
        tally.rejected.append(
            SkippedPair(
                left.path,
                right.path,
                "their bounding boxes are further apart than the clearance asked about, "
                "so the shapes inside them certainly are",
            )
        )
        return

    if tally.checked >= budget:
        tally.unchecked.append(
            SkippedPair(
                left.path,
                right.path,
                f"the pair budget of {budget} narrow-phase queries was reached before "
                "this pair. Raise budget=, or narrow the check with an ignore rule.",
            )
        )
        return

    tally.checked += 1
    try:
        report = measure(left, right)
    except Exception as exc:  # noqa: BLE001 - a measurer's failure is data, not a crash
        tally.unchecked.append(
            SkippedPair(left.path, right.path, f"{type(exc).__name__}: {exc}")
        )
        return

    tally.findings.append(
        ClashFinding(
            path_a=left.path,
            path_b=right.path,
            distance_mm=_as_float(getattr(report, "distance_mm", None)),
            interference_mm3=float(getattr(report, "interference_mm3", 0.0) or 0.0),
            failure=str(getattr(report, "failure", "") or ""),
        )
    )


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


# -- the OCCT adapters -------------------------------------------------------


def occt_bounds(shapes: Mapping[str, Any]) -> BoundsProvider:
    """A `BoundsProvider` reading each component's local bounding box from OCCT.

    `shapes` maps a component name to its `TopoDS_Shape` **in its own coordinates**. The
    kernel is imported inside the returned callable, not at module import, so this
    package loads and its structure and contract tests run on a machine with no OCCT —
    the discipline `app.design` keeps for the same reason.
    """

    def provider(component: str) -> Box:
        from app.kernel.occt.metrology import bounding_box_mm

        try:
            shape = shapes[component]
        except KeyError:
            raise KeyError(
                f"no shape was supplied for component {component!r}, so its bounding box "
                "cannot be measured. Supply one, or exclude the component from the check "
                "— a component with no geometry is not a component with no clashes."
            ) from None
        return Box.from_payload(bounding_box_mm(shape))

    return provider


def occt_measurer(shapes: Mapping[str, Any]) -> PairMeasurer:
    """A `PairMeasurer` that places each shape by its occurrence frame and measures.

    The transform is `BRepBuilderAPI_Transform` with `copy=True`. Copying is not
    optional: OCCT's transform without a copy shares the underlying `TShape` with the
    original, and a component instanced forty times at forty places would then be forty
    aliases of one moved shape. The cost is a shape copy per query, which is what buys
    the graph its correctness at the narrow phase.
    """
    placed: dict[str, Any] = {}

    def measurer(left: Occurrence, right: Occurrence) -> Any:
        from app.kernel.occt.interrogate.proximity import measure_clearance

        return measure_clearance(
            _placed_shape(shapes, placed, left), _placed_shape(shapes, placed, right)
        )

    return measurer


def _placed_shape(shapes: Mapping[str, Any], cache: dict[str, Any], occurrence: Occurrence) -> Any:
    """The occurrence's shape in world coordinates, cached per occurrence path.

    Cached because a pairwise check asks about each occurrence up to n-1 times and the
    transform is the expensive half of the query; keyed on the path rather than the
    component because two occurrences of one component are at two different places,
    which is the entire point.
    """
    hit = cache.get(occurrence.path)
    if hit is not None:
        return hit

    from app.kernel.occt.binding import symbol

    try:
        shape = shapes[occurrence.component]
    except KeyError:
        raise KeyError(
            f"no shape was supplied for component {occurrence.component!r} at "
            f"{occurrence.path}."
        ) from None

    rotation = occurrence.frame.rotation
    origin = occurrence.frame.origin_mm
    transform = symbol("gp_Trsf")()
    transform.SetValues(
        rotation[0], rotation[1], rotation[2], origin[0],
        rotation[3], rotation[4], rotation[5], origin[1],
        rotation[6], rotation[7], rotation[8], origin[2],
    )
    moved = symbol("BRepBuilderAPI_Transform")(shape, transform, True).Shape()
    cache[occurrence.path] = moved
    return moved


__all__ = [
    "DEFAULT_PAIR_BUDGET",
    "BoundsProvider",
    "ClashFinding",
    "ClashReport",
    "IgnoreRule",
    "PairMeasurer",
    "SkippedPair",
    "combine",
    "find_clashes",
    "occt_bounds",
    "occt_measurer",
    "same_parent",
    "touching_components",
]
