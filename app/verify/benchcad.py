"""Entering a public benchmark, and the arithmetic of its score (E23 task 4).

The phase's argument is that publishing a number — *including when it is bad* —
is a thing a company with a valuation to defend structurally cannot do first.
That only means anything if the number is the benchmark's own, computed its way,
so this module's first job is to record what the benchmark says about itself
from its own pages rather than from anyone's memory of it.

**What was read, and when.** `SOURCE` below carries the name, the two URLs, both
licences and the scoring rule, read on 2026-09-15. Every field is what those
pages state. Where a page's wording leaves something open, the gap is recorded
as a gap (see `SCORING_RULE_CAVEAT`) rather than closed by a plausible reading —
`app/verify/nafems.py`'s rule, applied to a benchmark instead of a target.

**The scoring rule, and why the two readings of it coincide.** BenchCAD's score
is *voxel Intersection over Union × execution success rate*. Read one way that
is `mean(IoU over the cases that executed) × (executed / total)`; read the other
it is `mean(IoU over every case, scoring a case that did not execute as zero)`.
Those are the same number — the first expands to `(ΣIoU / executed) ×
(executed / total)` = `ΣIoU / total` — so the ambiguity in the sentence does not
reach the arithmetic, and `score` says so rather than picking a side.

**What this module does not do.** It does not submit anything, and a score
computed here is not a leaderboard score: `NOT_SUBMITTED` is carried with every
report. It also does not itself drive Kryova's agent — running 106 part families
through a local model is hours of GPU time and belongs on the workstation (THE
QUEUE). What is here is the part that has to be right before any of that is
worth doing: the source record, the geometry comparison, and a refusal to report
a number when the dataset is not present.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import numpy as np

from app.kernel.occt.binding import symbol


@dataclass(frozen=True, slots=True)
class Source:
    """A benchmark described in its own words, with where and when it was read."""

    name: str
    paper_url: str
    site_url: str
    read_on: str
    code_licence: str
    data_licence: str
    scoring_rule: str
    scale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "paper_url": self.paper_url,
            "site_url": self.site_url,
            "read_on": self.read_on,
            "code_licence": self.code_licence,
            "data_licence": self.data_licence,
            "scoring_rule": self.scoring_rule,
            "scale": self.scale,
        }


#: BenchCAD, read 2026-09-15 from the two pages named below.
#:
#: The plan called this "an execution-verified CadQuery benchmark on 106
#: industrial part families" without naming it; this is that benchmark, found
#: and read rather than recalled.
SOURCE: Final = Source(
    name="BenchCAD: A Comprehensive, Industry-Standard Benchmark for Programmatic CAD",
    paper_url="https://arxiv.org/abs/2605.10865",
    site_url="https://benchcad.com/",
    read_on="2026-09-15",
    code_licence="MIT",
    data_licence="CC-BY-4.0",
    scoring_rule=(
        "IoU-score: voxel Intersection over Union multiplied by the execution success "
        "rate. The site states that grading is on executed geometry, not appearances, "
        "and offers both a no-tools evaluation and an agentic run in which the model may "
        "iterate in a Python sandbox before submitting."
    ),
    scale=(
        "17,900 execution-verified CadQuery programs across 106 industrial part families; "
        "52 of the 106 families are anchored to 47 ISO, DIN, EN, ASME and IEC standards."
    ),
)

#: What the pages did **not** settle, kept beside the rule rather than resolved.
#:
#: Read through a page extractor rather than by opening the repository, so the
#: voxel pitch, the alignment convention and whether IoU is computed on a
#: canonical pose are all unknown here. Each of them changes the number. Before
#: a score from this module is published anywhere, the benchmark's own scoring
#: code has to be read (its licence is MIT, so it can be).
SCORING_RULE_CAVEAT: Final = (
    "Read from the benchmark's abstract and site, not from its scoring code. The voxel "
    "pitch, the alignment convention and whether parts are posed canonically before "
    "comparison are unknown here, and each of them moves the number. A score computed by "
    "this module is therefore Kryova's arithmetic of the published rule, not a reproduction "
    "of the benchmark's own harness, until that code is read."
)

#: Carried with every report. A local run is not an entry.
NOT_SUBMITTED: Final = (
    "This score was computed locally and has not been submitted to the BenchCAD "
    "leaderboard. It is what this build scored on the cases that were run, not a ranked "
    "result, and the number of cases run is stated beside it."
)


class DatasetMissing(RuntimeError):
    """The dataset is not on this machine, so there is nothing to score.

    Raised rather than returning an empty report, because an empty report has a
    score-shaped hole in it that somebody eventually fills with a zero.
    """


class Pipeline(Protocol):
    """Whatever turns one case into a solid. Injected, like `app/design/execute`.

    A protocol rather than an import so this module never depends on the agent,
    the providers or a socket: the scoring arithmetic is testable with two boxes
    and no model at all, which is the only reason it can be trusted when a model
    is eventually attached to it.
    """

    def build(self, case: "Case") -> Any | None:
        """The solid this pipeline produced, or `None` if it produced none."""


@dataclass(frozen=True, slots=True)
class Case:
    """One benchmark case: a family, a part, and the reference solid's file."""

    id: str
    family: str
    reference_path: Path
    prompt: str = ""


@dataclass(frozen=True, slots=True)
class Attempt:
    """What happened on one case.

    `executed` is whether a solid came back at all — the benchmark's execution
    half. `iou` is `None` when nothing executed, never 0.0: the two are
    different facts, and `score` is the only place that is entitled to treat a
    non-execution as a zero.
    """

    case_id: str
    executed: bool
    iou: float | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.executed and self.iou is None:
            raise ValueError(
                f"Attempt {self.case_id!r} executed and carries no IoU. An executed case "
                "that was never compared is not a zero; score it or record it as failed."
            )
        if not self.executed and self.iou is not None:
            raise ValueError(
                f"Attempt {self.case_id!r} did not execute and carries an IoU."
            )


@dataclass(frozen=True, slots=True)
class Score:
    """The benchmark's number, with the denominator it was computed over."""

    cases: int
    executed: int
    iou_score: float | None
    mean_iou_where_executed: float | None
    execution_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": SOURCE.name,
            "source": SOURCE.to_dict(),
            "scoring_rule_caveat": SCORING_RULE_CAVEAT,
            "not_submitted": NOT_SUBMITTED,
            "cases": self.cases,
            "executed": self.executed,
            "execution_rate": self.execution_rate,
            "mean_iou_where_executed": self.mean_iou_where_executed,
            "iou_score": self.iou_score,
        }


def score(attempts: Sequence[Attempt]) -> Score:
    """The IoU-score over these attempts.

    Both readings of the published rule give the same number, so this computes
    it once and reports the two factors beside it: a reader who wants to know
    whether a low score is bad geometry or a failure to build can see which.

    Every rate is `None` over an empty denominator, for the reason
    `app/verify/horizon.py` gives: a zero is a measurement and the absence of
    one is not.
    """
    total = len(attempts)
    if not total:
        return Score(0, 0, None, None, None)

    executed = [one for one in attempts if one.executed]
    ious = [one.iou for one in executed if one.iou is not None]
    mean_where_executed = float(np.mean(ious)) if ious else None
    execution_rate = len(executed) / total
    # ΣIoU / total — identical to mean_where_executed × execution_rate, which is
    # why the ambiguity in the published sentence does not reach the arithmetic.
    iou_score = float(sum(ious) / total)
    return Score(
        cases=total,
        executed=len(executed),
        iou_score=iou_score,
        mean_iou_where_executed=mean_where_executed,
        execution_rate=execution_rate,
    )


def voxel_iou(
    candidate: Any,
    reference: Any,
    *,
    pitch_mm: float = 1.0,
    tolerance_mm: float = 1e-7,
) -> float:
    """Intersection over union of two solids, on a shared voxel grid.

    Both solids are sampled on **one** grid spanning the union of their bounding
    boxes. That is the whole of the correctness argument: a per-solid grid would
    put the two parts in different coordinate systems and produce a plausible
    number for two shapes that do not overlap at all.

    A point is inside when OCCT classifies it `IN` or `ON`. `ON` counts because
    a voxel centre landing exactly on a planar face is an artefact of the grid
    aligning with the geometry — which happens constantly on machined parts,
    where faces sit on round millimetres — and excluding it would score a part
    against an identical copy of itself at less than 1.0.

    Returns 0.0 when the union is empty, which is two empty solids, and 1.0 only
    for genuine agreement at this pitch. **The pitch is the measurement**: a
    coarse grid flatters agreement and a fine one costs cubically, so it is an
    argument with no default worth trusting, and the benchmark's own value is
    not known here (see `SCORING_RULE_CAVEAT`).
    """
    if pitch_mm <= 0:
        raise ValueError("pitch_mm must be positive; it is the edge of a sampling cube in mm.")

    low, high = _union_box(candidate, reference, tolerance_mm)
    if any(high[axis] <= low[axis] for axis in range(3)):
        return 0.0

    axes = [
        np.arange(low[axis] + pitch_mm / 2.0, high[axis], pitch_mm) for axis in range(3)
    ]
    if any(axis.size == 0 for axis in axes):
        return 0.0

    inside_candidate = _classify(candidate, axes, tolerance_mm)
    inside_reference = _classify(reference, axes, tolerance_mm)

    union = int(np.count_nonzero(inside_candidate | inside_reference))
    if not union:
        return 0.0
    intersection = int(np.count_nonzero(inside_candidate & inside_reference))
    return intersection / union


def _union_box(
    candidate: Any, reference: Any, tolerance_mm: float
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The bounding box covering both solids, slightly grown.

    Grown by the tolerance because a box that stops exactly on a face leaves the
    outermost layer of voxel centres outside the grid, which costs the candidate
    a sliver of its own volume and only on the faces that touch the box.
    """
    box_type = symbol("Bnd_Box")
    add = symbol("BRepBndLib").Add_s
    box = box_type()
    add(candidate, box, True)
    add(reference, box, True)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    pad = max(tolerance_mm, 1e-9)
    return (xmin - pad, ymin - pad, zmin - pad), (xmax + pad, ymax + pad, zmax + pad)


def _classify(solid: Any, axes: Sequence[Any], tolerance_mm: float) -> Any:
    """A boolean array over the grid: is each voxel centre inside this solid?

    One classifier, re-`Perform`ed per point. OCCT's solid classifier builds a
    spatial index on construction, so rebuilding it per point turns a scan into
    a quadratic one — measured by nobody here, but it is the documented reason
    the class separates construction from `Perform`.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON

    point_type = symbol("gp_Pnt")
    classifier = BRepClass3d_SolidClassifier(solid)
    xs, ys, zs = axes
    inside = np.zeros((xs.size, ys.size, zs.size), dtype=bool)
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            for k, z in enumerate(zs):
                classifier.Perform(point_type(float(x), float(y), float(z)), tolerance_mm)
                state = classifier.State()
                inside[i, j, k] = state == TopAbs_IN or state == TopAbs_ON
    return inside


def run(cases: Sequence[Case], pipeline: Pipeline, *, pitch_mm: float = 1.0) -> Score:
    """Build every case through the pipeline and score the results.

    Refuses an empty case list by name rather than returning a zero-case score:
    a report saying "0 cases, score 0.0" is the shape somebody screenshots.
    """
    if not cases:
        raise DatasetMissing(
            "No BenchCAD cases were given, so there is nothing to score. The dataset is "
            f"CC-BY-4.0 and is obtained from {SOURCE.site_url}; this module does not "
            "download it."
        )

    attempts: list[Attempt] = []
    for case in cases:
        try:
            built = pipeline.build(case)
        except Exception as exc:  # noqa: BLE001 - a failed build is a benchmark outcome
            attempts.append(Attempt(case.id, executed=False, error=str(exc)))
            continue
        if built is None:
            attempts.append(Attempt(case.id, executed=False, error="the pipeline built nothing"))
            continue
        reference = _load(case.reference_path)
        attempts.append(
            Attempt(case.id, executed=True, iou=voxel_iou(built, reference, pitch_mm=pitch_mm))
        )
    return score(attempts)


def _load(path: Path) -> Any:
    """One reference solid from disk, through this repository's existing reader.

    `app.manufacture.export.read_step`, which is the only STEP reader here and
    already handles the tessellation gotcha a file from elsewhere will trip.
    BenchCAD's references are CadQuery programs, so producing the STEP is part
    of obtaining the dataset and is not this module's job.
    """
    if not path.is_file():
        raise DatasetMissing(f"The reference part {path} is not on this machine.")
    from app.manufacture.export import read_step

    return read_step(path)


__all__ = [
    "Attempt",
    "Case",
    "DatasetMissing",
    "NOT_SUBMITTED",
    "Pipeline",
    "SCORING_RULE_CAVEAT",
    "SOURCE",
    "Score",
    "Source",
    "run",
    "score",
    "voxel_iou",
]
