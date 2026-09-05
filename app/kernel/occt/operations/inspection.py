"""Reading the part: measurement and analysis, without changing anything.

Two operations, and the split between them is the one `app.kernel.interrogation`
explains. `catia_measure` reports what the part **is** — always defined, always cheap
enough to run after every operation. `catia_analysis_part` asks what it can be **made
into**, which has a premise, can be inapplicable, and costs thousands of kernel calls.

`catia_measure` is deliberately more than a dimension read — it returns bounding box,
volume, mass, centre of gravity and surface area in one call, because those are what a
following FEA setup or a sanity check actually need, and three round trips to collect
them is three chances to go wrong. That reasoning is the registry's, and this honours it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import OperationNotSupported
from app.kernel.interrogation import InterrogationPayload
from app.kernel.measurement import Detail
from app.kernel.occt.operations.context import BuildContext

MEASURE = "catia_measure"
ANALYSIS = "catia_analysis_part"

#: The registry names a *plane* for the pull, which is how a CATIA user thinks about it —
#: "pulled off the XY plane". A pull direction is the plane's normal. Declared here rather
#: than derived so the mapping is visible: getting it wrong silently analyses draft
#: against the wrong axis and reports a plausible number for the wrong question.
_PULL_NORMALS: Final[dict[str, tuple[float, float, float]]] = {
    "XY": (0.0, 0.0, 1.0),
    "YZ": (1.0, 0.0, 0.0),
    "ZX": (0.0, 1.0, 0.0),
}

_DEFAULT_PULL: Final = "XY"


def measure(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Measure the part as it currently stands.

    An explicit `catia_measure` always computes at least `Detail.FULL`, whatever the
    runner's default post-state level is: a caller who asked to measure wants the
    numbers, and returning a cheaper payload because a batch replay lowered the default
    would silently answer a different question than the one asked.
    """
    document = context.require_document()
    detail = Detail.INERTIA if arguments.get("include_inertia") else Detail.FULL
    return document.measure(detail=detail)


def analysis_part(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Draft, wall thickness, curvature or validity, on the part as it stands.

    Every arm returns its numbers **with a provenance sidecar** saying whether they were
    measured or approximated, because they are not the same: validity and draft-on-planes
    are exact, thickness and curvature are sampled. A payload that flattened them into
    one confidence would make the exact numbers untrustworthy and the sampled ones
    overtrusted at the same time.

    `draft` also reports undercuts, which the registry has no separate kind for and which
    share its premise exactly — the same pull direction answers both, and "this face has
    good draft but the tool cannot reach it" is the answer a mould designer needs to see
    together rather than in two calls.
    """
    from app.kernel.occt import interrogate

    document = context.require_document()
    shape = document.shape
    if shape is None:
        raise OperationNotSupported(
            subject=ANALYSIS,
            reason=(
                "this document has no geometry yet, so there is nothing to analyse. "
                "Build a feature before asking about its draft or wall thickness"
            ),
            backend="occt",
        )

    kind = str(arguments.get("kind") or "").strip().lower()
    payload = InterrogationPayload()

    if kind == "thickness":
        report = interrogate.scan_thickness(shape)
        payload.add(report)
        minimum = _optional_float(arguments.get("minimum_mm"))
        if minimum is not None:
            payload.merge(_below_minimum(report, minimum))

    elif kind == "draft":
        pull = _pull_direction(arguments.get("direction"))
        required = _optional_float(arguments.get("minimum_mm")) or 0.0
        payload.add(interrogate.analyse_draft(shape, pull, required_deg=required))
        payload.add(interrogate.find_undercuts(shape, pull))

    elif kind == "curvature":
        payload.add(interrogate.scan_curvature(shape))
        payload.add(interrogate.scan_continuity(shape))

    elif kind == "validity":
        payload.add(interrogate.check_validity(shape))

    else:
        supported = ", ".join(sorted(_SUPPORTED_KINDS))
        raise OperationNotSupported(
            subject=ANALYSIS,
            reason=(
                f"{kind!r} is not an analysis this backend runs. Supported: {supported}"
            ),
            backend="occt",
        )

    payload.values["analysis_kind"] = kind
    return payload.as_dict()


#: What `analysis_part` implements, checked against the registry's own enum by the tests
#: so a kind added to the vocabulary cannot be silently left unimplemented here.
_SUPPORTED_KINDS: Final[frozenset[str]] = frozenset(
    {"thickness", "draft", "curvature", "validity"}
)


def _below_minimum(report: Any, minimum_mm: float) -> dict[str, Any]:
    """Which sampled points fall under a stated minimum wall.

    Reported as points rather than as a bare count: the registry's summary promises this
    finds walls "too thin to mould or print before they reach the shop floor", and a
    count alone sends someone hunting for them.
    """
    thin = [
        {"point_mm": list(sample.point), "thickness_mm": sample.thickness_mm}
        for sample in report.samples
        if sample.thickness_mm < minimum_mm
    ]
    return {
        "minimum_required_mm": minimum_mm,
        "below_minimum_count": len(thin),
        # Capped so a badly-thin part cannot return a payload of thousands of points.
        # The count above is complete; this is the sample to go and look at.
        "below_minimum_points": thin[:_MAX_REPORTED_POINTS],
        "below_minimum_truncated": len(thin) > _MAX_REPORTED_POINTS,
    }


#: How many offending points a thickness analysis lists. The count is always exact; this
#: bounds the payload, and `below_minimum_truncated` says when it bit.
_MAX_REPORTED_POINTS: Final = 20


def _pull_direction(value: Any) -> tuple[float, float, float]:
    if value is None:
        return _PULL_NORMALS[_DEFAULT_PULL]
    name = str(value).strip().upper()
    if name not in _PULL_NORMALS:
        allowed = ", ".join(sorted(_PULL_NORMALS))
        raise OperationNotSupported(
            subject=ANALYSIS,
            reason=(
                f"{value!r} is not a pull direction. Give the plane the part is drawn "
                f"off, one of: {allowed}"
            ),
            backend="occt",
        )
    return _PULL_NORMALS[name]


def _optional_float(value: Any) -> float | None:
    """A number the registry declares as a string, or None when absent or unreadable.

    `minimum_mm` is typed as a string in the operation schema because the CATIA daemon
    receives it that way. Refusing to parse it would make the argument unusable; guessing
    a default when it is malformed would silently analyse against a threshold nobody
    chose. None means "no threshold was given", and the analysis still reports its
    minimum.
    """
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


__all__ = ["ANALYSIS", "MEASURE", "analysis_part", "measure"]
