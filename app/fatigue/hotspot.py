"""The structural hot-spot stress at a weld toe, extrapolated from read-out points ahead of it.

Master plan E8.5. A stress read at a weld toe in a finite element model is not the stress the
hot-spot categories were calibrated against: at the toe the model reports a singularity whose
value depends on the mesh. The structural (geometric) stress method removes that peak by reading
the surface stress a little way ahead of the toe, where the weld's own notch no longer reaches,
and extrapolating back. Table B.1 of EN 1993-1-9 (`weld_catalogue.Joint.HOT_SPOT_*`) gives the
categories to use with the result. EN 1993-1-9 does not say where to read or how to extrapolate,
so that part comes from the IIW recommendations, read on 2026-09-15.

**Where the numbers come from.** Every distance and coefficient in `RULES` was read off a page of
IIW-1823-07 (§2.2.3.4, pp. 24–25, equations (2.7) to (2.11), and Table 2.2-2 on p. 26), with the
equations checked on 300 dpi page images because they are pictures with no text layer. The page
map is in `docs/iiw-hot-spot-and-neuber-reading.md`. None of it is recalled.

**What is applied is the extrapolation the page names, not its rounding.** IIW prints equation
(2.7) as σhs = 1.67·σ0.4t − 0.67·σ1.0t, which is linear extrapolation through two points written
to two decimals. The weights used here are the exact Lagrange weights through the rule's
reference points (5/3 and −2/3 for (2.7)); a test holds every one of them to its printed value at
the page's own precision. The printed pair would extrapolate a perfectly linear stress field to
the wrong value by 0.002 of its slope per plate thickness, which the exact weights do not.

**What the readings must be.**

* **At the rule's reference points.** A reading's position is checked against its reference
  point to floating-point round-off, not to an engineering tolerance, because the method is
  defined at those points and nothing on the page says how far off them it still holds. A mesh
  that has no node there must interpolate the stress to the point first; IIW §2.2.3.5 does the
  same for strain gauges ("the stresses at the required positions can then be read from the
  fitted curve"). Nothing here interpolates, so a caller that did says so in the reading's source.
* **Signed, sampled at the same instants, and local.** Extrapolation is linear in stress, so it
  is applied instant by instant and a range of the result is the extrapolated range. That only
  holds for one signed stress component: a von Mises norm cannot be extrapolated this way, and
  neither can readings sampled at different times. The readings are local surface stresses from a
  model or a gauge, which is `StressBasis.NOTCH_ROOT` in this package's vocabulary; a nominal
  stress has none of the structural concentration the method exists to capture, and a reading
  that is already a hot-spot stress would be extrapolated twice.
* **The stress across the toe.** IIW recommends the principal stress "within ±60°" of the
  perpendicular to the weld toe (p. 20). Choosing it is the caller's, through `field.Scalar`.

**What is not applied, and every result says so in its source.** IIW requires a wall-thickness
correction when a type "a" hot-spot stress is obtained by surface extrapolation (p. 24) and gives
one with exponent n = 0.1 for type "b" (p. 25), both on IIW's resistance side (§3.5.2, p. 77,
whose equation (3.6) was not read as an image and is not encoded). The categories this package
assesses with are EN 1993-1-9's, so the correction is not half-applied from the other document.
Misalignment is not in the extrapolation either: the stress is computed on an idealised, aligned
joint, and IIW says misalignment is modelled explicitly or applied as a factor km (p. 22).

Units are the codebase's mm-N-MPa; nothing here converts.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.fatigue.history import LoadHistory, SignConvention, StressBasis
from app.fatigue.sources import require_source

#: Where every rule below was read, in the words a reviewer can find it by.
IIW_SOURCE: Final[str] = (
    "IIW-1823-07 (ex XIII-2151r4-07/XV-1254r4-07), 'Recommendations for fatigue design of welded "
    "joints and components', A. Hobbacher, December 2008, §2.2.3.4 pp. 24–25 and Table 2.2-2 p. 26; "
    "read 2026-09-15 from https://svv.cz/files/IIW182307FatigueRecomm20121017.pdf"
)

#: How far a reading may sit from its reference point and still be called at it, relative to
#: that point's distance: the round-off of a coordinate written with seven significant figures
#: (a single-precision float, most mesh text formats). Not a tolerance on the method.
POSITION_ROUND_OFF: Final[float] = 1e-6


class HotSpotType(StrEnum):
    """IIW Table {2.2}-1 (p. 21): where the toe is, which decides how the reference points scale."""

    #: "Weld toe on plate surface". Reference points are multiples of the plate thickness t.
    A = "a"
    #: "Weld toe at plate edge". "The stress distribution is not dependent on plate thickness",
    #: so the reference points are absolute distances in mm (p. 25).
    B = "b"


class Method(StrEnum):
    """The five surface-extrapolation rules of IIW §2.2.3.4, numbered as the page numbers them."""

    FINE_LINEAR = "type a, fine mesh, 0.4t and 1.0t, linear (2.7)"
    FINE_QUADRATIC = "type a, fine mesh, 0.4t, 0.9t and 1.4t, quadratic (2.8)"
    COARSE_LINEAR = "type a, coarse mesh, 0.5t and 1.5t, linear (2.9)"
    EDGE_FINE_QUADRATIC = "type b, fine mesh, 4, 8 and 12 mm, quadratic (2.10)"
    EDGE_COARSE_LINEAR = "type b, coarse mesh, 5 and 15 mm, linear (2.11)"


@dataclass(frozen=True)
class Rule:
    """One extrapolation rule as the page states it."""

    method: Method
    hot_spot_type: HotSpotType
    #: Multiples of t for type a, millimetres for type b, nearest the toe first.
    reference_points: tuple[float, ...]
    #: The coefficients as printed, in the same order. Held against `weights` by a test.
    printed_coefficients: tuple[float, ...]
    equation: str
    page: int
    #: What the page says the mesh must be and where on it the stress is read.
    mesh: str

    @property
    def weights(self) -> tuple[float, ...]:
        """The exact Lagrange weights at the toe through the reference points.

        Scale-free: the weights through 0.4t and 1.0t are the same for every t, which is why a
        type "a" rule can state them once.
        """
        points = self.reference_points
        result = []
        for i, xi in enumerate(points):
            weight = 1.0
            for j, xj in enumerate(points):
                if j != i:
                    weight *= (0.0 - xj) / (xi - xj)
            result.append(weight)
        return tuple(result)

    def positions_mm(self, plate_thickness_mm: float | None) -> tuple[float, ...]:
        """Where the readings must be taken, in mm from the weld toe."""
        if self.hot_spot_type is HotSpotType.A:
            if plate_thickness_mm is None:
                raise ValueError(
                    f"{self.method} reads at multiples of the plate thickness t, so it needs "
                    "plate_thickness_mm (IIW §2.2.3.4, p. 24)."
                )
            if not (plate_thickness_mm > 0.0 and math.isfinite(plate_thickness_mm)):
                raise ValueError(f"The plate thickness must be positive and finite; got {plate_thickness_mm!r}.")
            return tuple(point * plate_thickness_mm for point in self.reference_points)
        if plate_thickness_mm is not None:
            raise ValueError(
                f"{self.method} reads at absolute distances, because at a plate edge 'the stress "
                "distribution is not dependent on plate thickness' (IIW p. 25). A thickness given "
                "here would be ignored, so it is refused."
            )
        return self.reference_points


RULES: Final[dict[Method, Rule]] = {
    rule.method: rule
    for rule in (
        Rule(
            Method.FINE_LINEAR, HotSpotType.A, (0.4, 1.0), (1.67, -0.67), "(2.7)", 24,
            "Fine mesh with element length not more than 0.4 t at the hot spot; nodal stresses at "
            "the reference points.",
        ),
        Rule(
            Method.FINE_QUADRATIC, HotSpotType.A, (0.4, 0.9, 1.4), (2.52, -2.24, 0.72), "(2.8)", 24,
            "Fine mesh as for (2.7); nodal stresses at the reference points. Recommended for a "
            "pronounced non-linear structural stress increase towards the hot spot, sharp changes "
            "of direction of the applied force, or thick-walled structures.",
        ),
        Rule(
            Method.COARSE_LINEAR, HotSpotType.A, (0.5, 1.5), (1.50, -0.50), "(2.9)", 24,
            "Coarse mesh with higher-order elements having lengths equal to plate thickness at the "
            "hot spot; stresses at mid-side points or surface centres.",
        ),
        Rule(
            Method.EDGE_FINE_QUADRATIC, HotSpotType.B, (4.0, 8.0, 12.0), (3.0, -3.0, 1.0), "(2.10)", 25,
            "Fine mesh with element length of not more than 4 mm at the hot spot; nodal stresses at "
            "the reference points.",
        ),
        Rule(
            Method.EDGE_COARSE_LINEAR, HotSpotType.B, (5.0, 15.0), (1.5, -0.5), "(2.11)", 25,
            "Coarse mesh with higher-order elements having length of 10 mm at the hot spot; "
            "stresses at the mid-side points of the first two elements.",
        ),
    )
}


@dataclass(frozen=True)
class Reading:
    """The surface stress history at one reference point ahead of the toe."""

    position_mm: float
    history: LoadHistory


@dataclass(frozen=True)
class HotSpotStress:
    """An extrapolated hot-spot history, and the rule and readings it came from."""

    history: LoadHistory
    rule: Rule
    positions_mm: tuple[float, ...]
    weights: tuple[float, ...]


def extrapolate(
    method: Method,
    readings: Sequence[Reading],
    *,
    plate_thickness_mm: float | None = None,
    name: str,
    location: str,
) -> HotSpotStress:
    """The structural hot-spot stress history at a weld toe, ready for a Table B.1 category.

    `location` names the toe — "stiffener end, frame 12, starboard". The readings may be given
    in any order; they are matched to the rule's reference points by position.
    """
    rule = RULES[method]
    where = require_source(location, "A hot-spot stress's weld toe")
    positions = rule.positions_mm(plate_thickness_mm)
    if len(readings) != len(positions):
        raise ValueError(
            f"{method} extrapolates from {len(positions)} reference points "
            f"({_listed(positions)} mm from the toe), and {len(readings)} readings were given."
        )

    ordered = sorted(readings, key=lambda reading: reading.position_mm)
    for reading, wanted in zip(ordered, positions, strict=True):
        if not math.isfinite(reading.position_mm) or abs(reading.position_mm - wanted) > POSITION_ROUND_OFF * wanted:
            raise ValueError(
                f"{method} reads the stress at {_listed(positions)} mm from the toe, and a reading was "
                f"taken at {reading.position_mm:g} mm. The method is defined at those points. "
                "Interpolate the stress to the reference point along the path first, and say so in "
                "that reading's source."
            )
        _check_reading(reading)

    lengths = {len(reading.history.values_mpa) for reading in ordered}
    if len(lengths) != 1:
        raise ValueError(
            "The readings hold different numbers of samples. The extrapolation is applied at each "
            "instant, so every reading must be sampled at the same instants."
        )

    weights = rule.weights
    samples = zip(*(reading.history.values_mpa for reading in ordered), strict=True)
    values = tuple(math.fsum(w * s for w, s in zip(weights, instant, strict=True)) for instant in samples)
    readings_text = "; ".join(
        f"{reading.position_mm:g} mm: {reading.history.source}" for reading in ordered
    )
    thickness_note = (
        "IIW requires a wall thickness correction for a type a hot spot obtained by surface "
        "extrapolation (p. 24)"
        if rule.hot_spot_type is HotSpotType.A
        else "IIW applies a wall thickness correction with n = 0.1 to a type b hot spot (p. 25)"
    )
    source = (
        f"structural hot-spot stress at {where}, {method}, exact Lagrange weights "
        f"{_listed(weights, '.4g')} (printed {_listed(rule.printed_coefficients)}) [{IIW_SOURCE}]; "
        f"readings — {readings_text}; not applied: {thickness_note}, on IIW's resistance side "
        "(§3.5.2), and misalignment, which the stress must already contain (p. 22)"
    )
    history = LoadHistory(
        name=name,
        values_mpa=values,
        basis=StressBasis.HOT_SPOT,
        sign=SignConvention.SIGNED,
        source=source,
    )
    return HotSpotStress(history=history, rule=rule, positions_mm=positions, weights=weights)


def _check_reading(reading: Reading) -> None:
    history = reading.history
    if history.sign is SignConvention.UNSIGNED:
        raise ValueError(
            f"The reading at {reading.position_mm:g} mm is unsigned, such as a von Mises norm. "
            "Extrapolating a norm point by point is not the extrapolation of any stress. Read one "
            "signed component across the weld toe."
        )
    if history.basis is StressBasis.HOT_SPOT:
        raise ValueError(
            f"The reading at {reading.position_mm:g} mm is already a hot-spot stress, and "
            "extrapolating it again would move it a second time."
        )
    if history.basis is StressBasis.NOMINAL:
        raise ValueError(
            f"The reading at {reading.position_mm:g} mm is a nominal stress, which holds none of the "
            "structural concentration the hot-spot method reads. Give the local surface stress "
            "from the model or gauge, as a notch-root history."
        )


def _listed(values: Sequence[float], spec: str = "g") -> str:
    return ", ".join(format(value, spec) for value in values)


__all__ = [
    "IIW_SOURCE",
    "POSITION_ROUND_OFF",
    "RULES",
    "HotSpotStress",
    "HotSpotType",
    "Method",
    "Reading",
    "Rule",
    "extrapolate",
]
