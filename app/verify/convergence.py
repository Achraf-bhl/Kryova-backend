"""Mesh convergence that decides for itself, and refuses when it cannot.

Master plan 7.2. Solve the same case on a sequence of grids, watch one quantity,
and answer the only question that matters: **is this number the model's answer,
or is it the mesh's?** After this module exists, an unconverged number cannot be
stated — `stated_value` returns `None` and `value_or_refuse()` raises. That is
the phase's own rule (Decision 3: "an unconverged number is worse than no
number") made mechanical rather than remembered.

## The procedure, and why this one

The estimator is the **Grid Convergence Index** of Roache, in the form
standardised by Celik et al. (2008, *J. Fluids Eng.* **130**(7), 078001) and
adopted by ASME V&V 20-2009. Three grids, a generalised (non-integer, non-equal)
refinement ratio, an *observed* order of convergence solved from the data, a
Richardson extrapolation to zero grid size, and an error band around the finest
answer.

Three properties earned it the job over the alternatives:

* **It measures the order rather than assuming it.** "Refine until it stops
  moving by 1%" cannot tell a converging sequence from a stalling one; an
  observed order can, and a negative or absent one is reported as a refusal.
* **It works on unstructured tetrahedra with whatever grid sizes gmsh actually
  produced.** The representative grid size is `h = (V/N)^(1/3)` — Celik's own
  definition for unstructured meshes — measured from the mesh, never from the
  size that was *asked* for. Asking gmsh for 4 mm and getting 3.7 mm is normal,
  and a ratio computed from the request would be wrong by exactly that much.

**The root must match the dimension of the model**, and this is the one piece of
arithmetic here that fails silently. Celik's `h = (V/N)^(1/3)` is the cube root
of a *volume* per element; the same statement for a plane model is the square
root of an *area* per element, `h = (A/N)^(1/2)`. Take the cube root of an area
and every refinement ratio comes back as `r^(2/3)` of the true one, so the
observed order — which is `ln|e32/e21| / ln r` — is inflated by exactly 3/2. A
second-order plane element then reports order 3.0, an entirely plausible number
that no reviewer could catch from the report, and the GCI band computed from it
is too narrow. `GridLevel` therefore carries its `dimension` and raises the
measure to `1/dimension`; the measure itself is stored under a name that says
its unit (`volume_mm3` or `area_mm2`) so the two can never be added up or
compared as one field.
* **It is a number a reviewer can argue with.** GCI is quoted as a percentage
  band on the fine-grid answer, with the safety factor and the observed order
  printed beside it.

## What "converged" means here, numerically

`GCI_fine <= 0.05` — a 5% band on the finest grid — with **every** one of these
also true, and each one is a refusal on its own:

| Requirement | Why it is not negotiable |
|---|---|
| at least three grids | Two grids cannot yield an order; you must *assume* one, and an assumed order is not a measurement. |
| every refinement ratio ≥ 1.1 | Below that the change between grids is dominated by mesh-generation noise rather than by discretisation. Celik recommends ≥ 1.3, which this module records as a caution rather than a refusal. |
| monotone convergence (`ε32/ε21 > 0`) | Richardson extrapolation assumes a single leading error term. An oscillating sequence violates that, so the extrapolated value and the GCI are both meaningless — the classic way to report a confident, wrong number. |
| `0 < p <= 6` | A negative order is divergence. An implausibly large one means the differences are noise, not error, and it makes `r^p - 1` huge, so the GCI collapses towards zero and reports false confidence. |

The safety factor is **1.25**, Celik's value for three or more grids; it is
calibrated so the band approximates 95% confidence. Roache's 3.0 applies to a
two-grid study with an *assumed* order, which this module refuses outright, so
that factor never appears.

**5% is a default, not a law.** It is the customary engineering acceptance for a
structural quantity of interest, it is a constructor argument, and the threshold
that was applied is recorded in the study so a reader never has to guess which
one was used. A fatigue life needs tighter; a first-pass stiffness check does
not.

## What it deliberately does not do

It does not refine on its own initiative until it passes. The grid sequence is
the caller's, because "refine until converged" on a part with a re-entrant
corner is an infinite loop: the peak stress at a singularity genuinely does not
converge, and the honest output there is `NOT_CONVERGED`, not a bigger machine.
The refusal message names the next grid size to try, which is the useful half of
automation without the runaway half.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.mesh.types import MeshError
from app.solve.types import SolverError

#: Celik's safety factor for a three-or-more-grid study with an observed order.
SAFETY_FACTOR = 1.25

#: Default acceptance band on the fine-grid answer, as a fraction.
DEFAULT_GCI_THRESHOLD = 0.05

#: Below this refinement ratio the grids are too close to separate discretisation
#: error from mesh-generation noise, and the study refuses.
MINIMUM_REFINEMENT_RATIO = 1.1

#: Celik's recommended minimum. Between this and MINIMUM_REFINEMENT_RATIO the
#: study still reports, with a caution.
RECOMMENDED_REFINEMENT_RATIO = 1.3

#: An observed order above this is not a convergence rate, it is noise being
#: fitted. `r^p - 1` grows fast enough that such a p drives GCI to nearly zero,
#: which is false confidence rather than a good result.
MAXIMUM_CREDIBLE_ORDER = 6.0

#: Relative change below which two grids are taken to have given the same
#: answer. Well above float64 round-off, well below any real discretisation
#: difference, so it cannot be reached by accident on a moving quantity.
NEGLIGIBLE_CHANGE = 1e-10

#: How far the asymptotic-range indicator may sit from 1.0 before it is worth
#: mentioning. Not a refusal: it is an indicator, and Celik treats it as one.
_ASYMPTOTIC_BAND = 0.15


class Verdict(StrEnum):
    """The three answers a convergence study can give.

    Deliberately the same shape as `app.design.assertions`' pass / fail /
    unmeasured, and for the same reason: a study that could not be assessed must
    not be reported as one that passed. Only `CONVERGED` yields a number.
    """

    CONVERGED = "converged"
    NOT_CONVERGED = "not converged"
    INDETERMINATE = "indeterminate"


class UnconvergedError(RuntimeError):
    """A converged value was demanded from a study that has none."""


#: The unit each spatial dimension measures its domain in. The key is the
#: dimension, the value is the unit string a caller may ask `GridLevel.measure`
#: for; there is deliberately no entry for 1, because a beam study's
#: representative size is a length and needs no root at all.
MEASURE_UNIT: dict[int, str] = {2: "mm^2", 3: "mm^3"}


@dataclass(frozen=True, kw_only=True)
class GridLevel:
    """One grid in a study: what was meshed, in how many dimensions, and what it
    said.

    `element_size_mm` is what was *asked* for and is recorded only for the
    report and for the "try this next" message. Every ratio in the maths uses
    `representative_size_mm`, which is measured from the mesh that was actually
    produced.

    ## Why the measure is two fields rather than one

    The obvious design is a single `measure` field holding mm^3 on a solid level
    and mm^2 on a plane one. That is exactly the unit blur this codebase refuses
    everywhere else — `Quantity` keeps a unit string precisely so "a target
    quoted in MPa can never be silently compared against a value read in mm" —
    and it is worse here than usual, because the two are *numerically
    comparable*: a 1000 mm^3 level and a 1000 mm^2 level would sort, subtract
    and average without a murmur, and the resulting representative size would be
    wrong by a factor nobody could see.

    So the measure is stored under a name that carries its unit. Exactly one of
    `volume_mm3` and `area_mm2` is present, `dimension` says which, and
    `__post_init__` refuses any other combination — a plane level offered a
    volume is a modelling mistake, not a field to coerce. Reading the wrong one
    yields `None`, which is the same refusal `stated_value` makes: arithmetic on
    it raises rather than producing a plausible figure. `measure()` is the
    dimension-agnostic read and it makes you name the unit you expect, so a
    caller that asks a plane level for mm^3 is told so instead of answered.

    `dimension` defaults to 3 because every study that existed before plane
    models did was a solid one, including the recorded validation artefact.
    """

    element_size_mm: float
    node_count: int
    element_count: int
    element_type: str
    value: float
    #: 3 for a solid (tet) mesh, 2 for a plane (tri) mesh. Fixes the root taken
    #: in `representative_size_mm`; see the module docstring for what a wrong
    #: one does to the observed order.
    dimension: int = 3
    #: Total meshed volume, mm^3. Present on a 3-D level and `None` on a 2-D one.
    volume_mm3: float | None = None
    #: Total meshed area, mm^2. Present on a 2-D level and `None` on a 3-D one.
    area_mm2: float | None = None

    def __post_init__(self) -> None:
        if self.dimension not in MEASURE_UNIT:
            raise ValueError(
                f"A grid level is 2-D (a plane mesh, measured in mm^2) or 3-D (a solid "
                f"mesh, measured in mm^3); got dimension={self.dimension}. A 1-D study "
                "would need no root at all and is not supported here."
            )
        if self.dimension == 3:
            if self.volume_mm3 is None:
                raise ValueError(
                    "A 3-D grid level is described by its meshed volume; pass "
                    "volume_mm3. Without it there is no measure to take a cube root of "
                    "and the level cannot be placed on a refinement axis."
                )
            if self.area_mm2 is not None:
                raise ValueError(
                    "A 3-D grid level carries volume_mm3, not area_mm2. Two measures on "
                    "one level is how mm^2 ends up being cube-rooted; pass exactly the "
                    "one that matches the dimension."
                )
        else:
            if self.area_mm2 is None:
                raise ValueError(
                    "A 2-D grid level is described by its meshed area; pass area_mm2. "
                    "Without it there is no measure to take a square root of and the "
                    "level cannot be placed on a refinement axis."
                )
            if self.volume_mm3 is not None:
                raise ValueError(
                    "A 2-D grid level carries area_mm2, not volume_mm3. A plane model "
                    "has no meshed volume — the out-of-plane extent is an idealisation "
                    "the solver supplies, not something the mesh knows."
                )

    @property
    def measure_unit(self) -> str:
        """`mm^3` on a solid level, `mm^2` on a plane one."""
        return MEASURE_UNIT[self.dimension]

    def measure(self, unit: str) -> float:
        """The level's d-dimensional measure, in the unit you name.

        You name the unit and it is checked, which is the whole defence this
        design exists to provide: the number is meaningless without one, and a
        caller that asks a plane level for a volume has asked a question with no
        answer and is told so rather than handed an area.
        """
        if unit != self.measure_unit:
            raise ValueError(
                f"This is a {self.dimension}-D grid level, measured in "
                f"{self.measure_unit}; {unit!r} was asked for. A plane mesh has no "
                "volume and a solid mesh has no single area, so there is no number to "
                "return and converting one into the other is not defined."
            )
        measure = self.volume_mm3 if self.dimension == 3 else self.area_mm2
        if measure is None:  # pragma: no cover - __post_init__ forbids it
            raise ValueError(f"This grid level carries no {self.measure_unit}.")
        return float(measure)

    @property
    def representative_size_mm(self) -> float:
        """`h = (M/N)^(1/d)` — Celik's representative cell size for an
        unstructured mesh, with `M` the meshed measure, `N` the element count
        and `d` the dimension of the model.

        The average edge length a cell of this mesh would have if the cells were
        cubes (3-D) or squares (2-D), which is the only mesh-independent way to
        put an unstructured grid on a refinement axis.

        **The root must match the dimension, and getting it wrong is silent.**
        `h` only behaves like a length — halving when the elements halve — if
        the measure is reduced by the root of its own dimension. Cube-rooting an
        area yields `h ∝ (A/N)^(1/3)`, so every refinement ratio comes out as
        the true one to the power 2/3 and the observed order, `ln|e32/e21|/ln r`,
        is inflated by exactly 3/2. Nothing downstream can detect that: the
        verdict, the extrapolation and the GCI band are all arithmetic on ratios
        that are individually plausible.
        """
        measure = self.volume_mm3 if self.dimension == 3 else self.area_mm2
        if self.element_count <= 0 or measure is None or measure <= 0.0:
            raise ValueError(
                f"A grid level with no elements or no {self.measure_unit} has no "
                "representative size. The mesh for this level did not build."
            )
        return float((measure / self.element_count) ** (1.0 / self.dimension))

    def to_dict(self) -> dict[str, Any]:
        """The machine-readable form.

        Emits the measure under its own unit-bearing key — `volume_mm3` for a
        solid level, exactly as it always did, so an existing reader of the
        recorded artefact is unaffected — and never both.
        """
        payload: dict[str, Any] = {
            "element_size_mm": self.element_size_mm,
            "representative_size_mm": self.representative_size_mm,
            "node_count": self.node_count,
            "element_count": self.element_count,
            "element_type": self.element_type,
            "dimension": self.dimension,
            "value": self.value,
        }
        if self.dimension == 3:
            payload["volume_mm3"] = self.volume_mm3
        else:
            payload["area_mm2"] = self.area_mm2
        return payload


@dataclass(frozen=True)
class ConvergenceStudy:
    """The evidence for — or against — one number.

    Every field a reviewer needs to redo the arithmetic is here, including the
    threshold that was applied and the levels the answer came from.
    """

    quantity: str
    unit: str
    #: Finest first. `assess` sorts them, so the caller's order does not matter.
    levels: tuple[GridLevel, ...]
    verdict: Verdict
    reason: str
    gci_threshold: float
    observed_order: float | None = None
    extrapolated_value: float | None = None
    gci_fine: float | None = None
    refinement_ratios: tuple[float, ...] = ()
    asymptotic_ratio: float | None = None
    cautions: tuple[str, ...] = ()
    #: Non-empty only for `INDETERMINATE` studies whose levels failed to build
    #: or to solve. The message is the mesher's or the solver's own.
    failures: tuple[str, ...] = field(default=())

    @property
    def fine_value(self) -> float | None:
        """The finest grid's raw answer, converged or not.

        Present so a diagnostic can show what the model said; **not** a value
        anything may quote as a result. `stated_value` is the one that is
        allowed out, and it is `None` unless the study converged.
        """
        return self.levels[0].value if self.levels else None

    @property
    def stated_value(self) -> float | None:
        """The number this study permits anyone to state, or `None`.

        `None` for every verdict but `CONVERGED`. This is the whole of 7.2 in
        one property: a caller that wants a number and gets `None` has been told
        the honest answer, and a caller that ignores the `None` gets a
        `TypeError` from arithmetic rather than a plausible wrong figure.
        """
        if self.verdict is not Verdict.CONVERGED:
            return None
        return self.levels[0].value

    def value_or_refuse(self) -> float:
        """The stated value, or raise with what to do next.

        The refusing form, for the report machinery: a caller that must have a
        number gets an exception naming the grid size that would extend the
        study, rather than a `None` it might coerce.
        """
        value = self.stated_value
        if value is None:
            raise UnconvergedError(self.report())
        return value

    def next_element_size_mm(self) -> float | None:
        """A grid size worth adding to this study, or `None` if it converged.

        The finest size divided by the recommended ratio, so the new level
        extends the sequence rather than crowding the one that is already there.
        """
        if self.verdict is Verdict.CONVERGED or not self.levels:
            return None
        return self.levels[0].element_size_mm / RECOMMENDED_REFINEMENT_RATIO

    def report(self) -> str:
        """One paragraph a person can act on. Says what to do next."""
        head = f"{self.quantity}: {self.verdict}."
        if self.verdict is Verdict.CONVERGED:
            band = "" if self.gci_fine is None else f" ±{self.gci_fine * 100:.2f}% (GCI)"
            order = "" if self.observed_order is None else f", observed order {self.observed_order:.2f}"
            return (
                f"{head} {self.levels[0].value:.6g} {self.unit}{band}{order}, "
                f"over {len(self.levels)} grids. {self.reason}"
            ).strip()

        detail = ", ".join(
            f"h={level.representative_size_mm:.3g} mm -> {level.value:.6g} {self.unit}"
            for level in self.levels
        )
        nxt = self.next_element_size_mm()
        advice = (
            ""
            if nxt is None
            else f" Add a grid at element_size_mm <= {nxt:.4g} and assess again."
        )
        return f"{head} {self.reason} Grids: {detail or 'none'}.{advice} No value may be stated."

    def to_dict(self) -> dict[str, Any]:
        """The machine-readable form, for a provenance record or the register."""
        return {
            "quantity": self.quantity,
            "unit": self.unit,
            "verdict": str(self.verdict),
            "reason": self.reason,
            "gci_threshold": self.gci_threshold,
            "gci_fine": self.gci_fine,
            "observed_order": self.observed_order,
            "extrapolated_value": self.extrapolated_value,
            "asymptotic_ratio": self.asymptotic_ratio,
            "refinement_ratios": list(self.refinement_ratios),
            "safety_factor": SAFETY_FACTOR,
            "stated_value": self.stated_value,
            "fine_value": self.fine_value,
            "levels": [level.to_dict() for level in self.levels],
            "cautions": list(self.cautions),
            "failures": list(self.failures),
        }


def _sorted_fine_first(levels: Sequence[GridLevel]) -> tuple[GridLevel, ...]:
    return tuple(sorted(levels, key=lambda level: level.representative_size_mm))


def observed_order(
    e21: float, e32: float, r21: float, r32: float, *, iterations: int = 200
) -> float | None:
    """Solve Celik's transcendental equation for the observed order `p`.

        p = (ln|e32/e21| + q(p)) / ln(r21),
        q(p) = ln((r21^p - s) / (r32^p - s)),   s = sign(e32/e21)

    Fixed-point iteration from `q = 0`, which is the exact answer when the two
    refinement ratios are equal — so on a constant-ratio study this converges on
    the first pass and reduces to the textbook three-grid formula.

    **The order returned is signed, and that is a deliberate departure from the
    published form.** Celik writes the numerator inside an absolute value,
    because his equation is posed for a sequence that is already known to be
    converging. Taking it literally is a trap: on a *diverging* sequence — one
    where refining the mesh moves the answer further each time, so
    `|e32| < |e21|` — the logarithm is negative and the absolute value mirrors it
    back onto a perfectly plausible positive order. Measured on this codebase
    before the sign was restored: the sequence 100.03 -> 100.02 -> 100.00, whose
    steps *grow* under refinement, came back `CONVERGED` at "observed order 1.00,
    ±0.03% (GCI)" and permitted the value to be stated. A diverging sequence must
    produce a negative order, which `assess` refuses; the absolute value made
    `assess`'s own "not positive" branch dead code and turned divergence into
    exactly the confident wrong number this module exists to prevent.

    Returns `None` when the iteration cannot be carried out at all: a zero
    denominator, a non-finite step, or a runaway `p`. `None` is "the order could
    not be estimated", which the caller turns into a refusal — never into a
    default order silently assumed.
    """
    if e21 == 0.0 or r21 <= 1.0 or r32 <= 1.0:
        return None
    ratio = e32 / e21
    if ratio == 0.0 or not math.isfinite(ratio):
        return None
    sign = 1.0 if ratio > 0.0 else -1.0
    log_ratio = math.log(abs(ratio))
    log_r21 = math.log(r21)

    p = log_ratio / log_r21
    for _ in range(iterations):
        try:
            denominator = r32**p - sign
            numerator = r21**p - sign
            if denominator == 0.0 or numerator / denominator <= 0.0:
                return None
            q = math.log(numerator / denominator)
            updated = (log_ratio + q) / log_r21
        except (ValueError, OverflowError, ZeroDivisionError):
            return None
        if not math.isfinite(updated) or abs(updated) > 100.0:
            return None
        if abs(updated - p) < 1e-10:
            return updated
        p = updated
    return None


def assess(
    quantity: str,
    unit: str,
    levels: Sequence[GridLevel],
    *,
    gci_threshold: float = DEFAULT_GCI_THRESHOLD,
    formal_order: float | None = None,
    failures: Sequence[str] = (),
) -> ConvergenceStudy:
    """Turn a set of grid results into a verdict. Pure arithmetic; no solving.

    Separated from `run_study` so the decision rules can be tested against
    hand-written sequences — a converging one, a stalling one, an oscillating
    one — without meshing anything. Every refusal in this function is reachable
    from `tests/test_verify_convergence.py`.

    `formal_order` is the theoretical order of the element, used only to raise a
    caution when the observed order is far from it. It never changes the verdict:
    the observed order is the measurement, and overriding a measurement with the
    value it was expected to have is how a study stops being one.
    """
    if not 0.0 < gci_threshold < 1.0:
        raise ValueError(
            f"gci_threshold is a fraction of the fine-grid value and must lie in "
            f"(0, 1); got {gci_threshold}. 0.05 is a 5% band."
        )

    def indeterminate(reason: str, **extra: Any) -> ConvergenceStudy:
        return ConvergenceStudy(
            quantity=quantity,
            unit=unit,
            levels=ordered,
            verdict=Verdict.INDETERMINATE,
            reason=reason,
            gci_threshold=gci_threshold,
            failures=tuple(failures),
            **extra,
        )

    try:
        ordered = _sorted_fine_first(levels)
    except ValueError as exc:
        return ConvergenceStudy(
            quantity=quantity,
            unit=unit,
            levels=(),
            verdict=Verdict.INDETERMINATE,
            reason=str(exc),
            gci_threshold=gci_threshold,
            failures=tuple(failures),
        )

    if len(ordered) < 3:
        return indeterminate(
            f"Only {len(ordered)} grid(s) completed; three are needed to observe an "
            "order of convergence. Two grids can only give an error estimate against "
            "an order that was assumed rather than measured, and an assumed order is "
            "not evidence. Add a coarser or finer grid and assess again."
        )

    fine, medium, coarse = ordered[0], ordered[1], ordered[2]
    h1, h2, h3 = (
        fine.representative_size_mm,
        medium.representative_size_mm,
        coarse.representative_size_mm,
    )
    f1, f2, f3 = fine.value, medium.value, coarse.value
    r21, r32 = h2 / h1, h3 / h2
    ratios = (r21, r32)

    if min(ratios) < MINIMUM_REFINEMENT_RATIO:
        return indeterminate(
            f"The grids are too close together (refinement ratios {r21:.3f} and "
            f"{r32:.3f}; at least {MINIMUM_REFINEMENT_RATIO} is required). Below that "
            "the change between grids is mesh-generation noise rather than "
            "discretisation error. Spread the element sizes further apart.",
            refinement_ratios=ratios,
        )

    cautions: list[str] = []
    if min(ratios) < RECOMMENDED_REFINEMENT_RATIO:
        cautions.append(
            f"Refinement ratios {r21:.3f}/{r32:.3f} are below the recommended "
            f"{RECOMMENDED_REFINEMENT_RATIO}; the observed order is less reliable."
        )

    scale = max(abs(f1), abs(f2), abs(f3))
    if scale == 0.0:
        return indeterminate(
            "Every grid returned exactly zero, so there is no change to measure and "
            "no relative error to bound. If zero is the expected answer, assert it "
            "directly rather than converging on it.",
            refinement_ratios=ratios,
        )

    e21 = f2 - f1
    e32 = f3 - f2

    if abs(e21) / scale < NEGLIGIBLE_CHANGE and abs(e32) / scale < NEGLIGIBLE_CHANGE:
        return ConvergenceStudy(
            quantity=quantity,
            unit=unit,
            levels=ordered,
            verdict=Verdict.CONVERGED,
            reason=(
                "All three grids agree to within 1e-10 of the value, so the quantity "
                "is grid-independent. The order of convergence is not observable when "
                "there is no error to fit, and none is claimed."
            ),
            gci_threshold=gci_threshold,
            observed_order=None,
            extrapolated_value=f1,
            gci_fine=0.0,
            refinement_ratios=ratios,
            cautions=tuple(cautions),
            failures=tuple(failures),
        )

    if e21 == 0.0:
        return indeterminate(
            "The two finest grids gave the same value while the coarsest differed, so "
            "the error ratio is undefined and no order can be fitted. Add a finer "
            "grid.",
            refinement_ratios=ratios,
        )

    def not_converged(reason: str, **extra: Any) -> ConvergenceStudy:
        return ConvergenceStudy(
            quantity=quantity,
            unit=unit,
            levels=ordered,
            verdict=Verdict.NOT_CONVERGED,
            reason=reason,
            gci_threshold=gci_threshold,
            refinement_ratios=ratios,
            cautions=tuple(cautions),
            failures=tuple(failures),
            **extra,
        )

    if e32 / e21 < 0.0:
        return not_converged(
            f"The quantity oscillates between grids ({f3:.6g} -> {f2:.6g} -> {f1:.6g} "
            f"{unit}), so it is not converging monotonically. Richardson extrapolation "
            "assumes one leading error term and does not hold here, so neither an "
            "extrapolated value nor a GCI would mean anything. Refine further, or "
            "check the mesh quality on the grids either side of the reversal."
        )

    order = observed_order(e21, e32, r21, r32)
    if order is None:
        return indeterminate(
            "The observed order of convergence could not be solved from these three "
            "grids. Spread the element sizes further apart and assess again.",
            refinement_ratios=ratios,
        )

    if order <= 0.0:
        return not_converged(
            f"The observed order of convergence is {order:.3f}, which is not positive: "
            f"refining the mesh is not reducing the error. The coarse-to-medium step is "
            f"{e32:.6g} {unit} and the medium-to-fine step is {e21:.6g} {unit}, so the "
            "change is holding or growing as the mesh refines rather than shrinking. "
            "Check the mesh quality and the boundary conditions before refining further; "
            "no extrapolation is offered, because Richardson extrapolation of a "
            "diverging sequence produces a number with no meaning at all.",
            observed_order=order,
        )

    if order > MAXIMUM_CREDIBLE_ORDER:
        return not_converged(
            f"The observed order of convergence is {order:.3f}, above the credible "
            f"ceiling of {MAXIMUM_CREDIBLE_ORDER}. Differences that large between "
            "grids are being fitted as if they were discretisation error; the GCI "
            "computed from such an order collapses towards zero and would report "
            "confidence that is not there. Re-run with grids further apart.",
            observed_order=order,
        )

    extrapolated = (r21**order * f1 - f2) / (r21**order - 1.0)
    approximate_error_21 = abs(e21 / f1) if f1 != 0.0 else abs(e21) / scale
    gci_fine = SAFETY_FACTOR * approximate_error_21 / (r21**order - 1.0)

    approximate_error_32 = abs(e32 / f2) if f2 != 0.0 else abs(e32) / scale
    gci_medium = SAFETY_FACTOR * approximate_error_32 / (r32**order - 1.0)
    asymptotic = (
        gci_medium / (r21**order * gci_fine) if gci_fine > 0.0 else None
    )
    if asymptotic is not None and abs(asymptotic - 1.0) > _ASYMPTOTIC_BAND:
        cautions.append(
            f"The asymptotic-range indicator is {asymptotic:.3f} rather than ~1.0, so "
            "these grids may not yet be in the asymptotic range and the GCI may "
            "understate the error."
        )

    if formal_order is not None and abs(order - formal_order) > 1.0:
        cautions.append(
            f"The observed order {order:.2f} differs from the element's formal order "
            f"{formal_order:.2f} by more than 1.0."
        )

    common = {
        "observed_order": order,
        "extrapolated_value": extrapolated,
        "gci_fine": gci_fine,
        "asymptotic_ratio": asymptotic,
    }

    if gci_fine > gci_threshold:
        return not_converged(
            f"The fine-grid GCI is {gci_fine * 100:.2f}%, above the {gci_threshold * 100:.2f}% "
            f"band this study requires. The answer is still moving with the mesh: "
            f"Richardson extrapolates to {extrapolated:.6g} {unit} against a fine-grid "
            f"{f1:.6g} {unit}.",
            **common,
        )

    return ConvergenceStudy(
        quantity=quantity,
        unit=unit,
        levels=ordered,
        verdict=Verdict.CONVERGED,
        reason=(
            f"The fine-grid GCI is {gci_fine * 100:.2f}%, within the "
            f"{gci_threshold * 100:.2f}% band, with a monotone observed order of "
            f"{order:.2f} over {len(ordered)} grids."
        ),
        gci_threshold=gci_threshold,
        refinement_ratios=ratios,
        cautions=tuple(cautions),
        failures=tuple(failures),
        **common,
    )


@runtime_checkable
class SolidMesh(Protocol):
    """The part of a solid mesh a grid level is built from.

    Structural, not nominal, and that is the layering rule rather than a
    preference: `app.verify` may not import `app.mesh.planar` (or any other
    concrete mesh module) to find out what a mesh is, because the study is
    supposed to have no opinion about which mesher produced the numbers — the
    same reason the sampler is injected. A protocol lets a surrogate, a
    CalculiX-side mesh or a future hex mesh satisfy the study without an edit
    here.
    """

    @property
    def node_count(self) -> int: ...

    @property
    def element_type(self) -> str: ...

    @property
    def tet_count(self) -> int: ...

    @property
    def volume(self) -> float: ...


@runtime_checkable
class PlaneMesh(Protocol):
    """The part of a plane mesh a grid level is built from.

    Disjoint from `SolidMesh` by accident of history and kept that way on
    purpose: `TetMesh` spells its element count `tet_count` and has no
    `element_count`, `TriMesh` spells it `element_count` and has no `volume`, so
    no object can satisfy both and the dimension is decided by which one matches
    rather than by anybody remembering to pass a flag.
    """

    @property
    def node_count(self) -> int: ...

    @property
    def element_type(self) -> str: ...

    @property
    def element_count(self) -> int: ...

    @property
    def area(self) -> float: ...


#: Either kind of mesh a study can be run on.
StudyMesh = SolidMesh | PlaneMesh

#: What a study asks of its caller for one grid size: build the mesh you want at
#: that target size, solve, and hand back the mesh you actually made together
#: with the quantity you read off it. Returning the mesh rather than a node count
#: is the point — the representative grid size is measured from it, so a level
#: can never be described by a mesh other than the one it was solved on.
Sampler = Callable[[float], tuple[StudyMesh, float]]


def _level_from(mesh: object, *, element_size_mm: float, value: float) -> GridLevel:
    """Describe one solved mesh as a grid level, in whatever dimension it is.

    Refuses **by name** rather than raising `AttributeError`, because the two
    read completely differently in a study log: an `AttributeError` on
    `tet_count` looks like a bug in this module, and the actual fault is a
    sampler handing back something that is not a mesh this study can measure.
    """
    if isinstance(mesh, SolidMesh):
        return GridLevel(
            element_size_mm=element_size_mm,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            element_type=mesh.element_type,
            dimension=3,
            volume_mm3=mesh.volume,
            value=value,
        )
    if isinstance(mesh, PlaneMesh):
        return GridLevel(
            element_size_mm=element_size_mm,
            node_count=mesh.node_count,
            element_count=mesh.element_count,
            element_type=mesh.element_type,
            dimension=2,
            area_mm2=mesh.area,
            value=value,
        )

    solid_missing = [
        name
        for name in ("node_count", "element_type", "tet_count", "volume")
        if not hasattr(mesh, name)
    ]
    plane_missing = [
        name
        for name in ("node_count", "element_type", "element_count", "area")
        if not hasattr(mesh, name)
    ]
    raise TypeError(
        f"The sampler returned a {type(mesh).__name__}, which this study cannot place "
        f"on a refinement axis. A solid mesh must expose node_count, element_type, "
        f"tet_count and volume (mm^3) — missing {', '.join(solid_missing) or 'nothing'}; "
        f"a plane mesh must expose node_count, element_type, element_count and area "
        f"(mm^2) — missing {', '.join(plane_missing) or 'nothing'}. Add the missing "
        "members, or hand back a mesh type that already has them."
    )


def run_study(
    quantity: str,
    unit: str,
    element_sizes_mm: Sequence[float],
    sample: Sampler,
    *,
    gci_threshold: float = DEFAULT_GCI_THRESHOLD,
    formal_order: float | None = None,
) -> ConvergenceStudy:
    """Run one sampler over a sequence of grid sizes and assess the result.

    The sampler is injected rather than assembled here for the same reason
    `app.design.execute` takes a callable: this module then has no opinion about
    which mesher, which solver or which geometry produced the numbers, so a
    CalculiX run, an in-process run and a surrogate all converge the same way.

    **The dimension comes from the mesh, not from an argument.** A sampler
    returning a solid mesh produces 3-D levels and a sampler returning a plane
    mesh produces 2-D ones, so a plane study cannot be assessed with a solid
    study's cube root by forgetting a flag. A returned object that is neither is
    a `TypeError` naming the members it lacks — it is a bug in the sampler, not
    a level that failed to build, and it must not be recorded as a failure that
    a longer grid sequence would fix.

    A level that fails to mesh or to solve is **recorded, not raised**. One
    element size being too coarse to mesh a fillet is normal and should not
    destroy the study; three levels still surviving is enough to assess, and if
    they do not the verdict is `INDETERMINATE` with the mesher's own message
    carried in `failures`. Anything that is not a `MeshError` or a `SolverError`
    propagates, because a `TypeError` in a sampler is a bug in the caller and
    swallowing it would turn it into a mysterious non-convergence.
    """
    levels: list[GridLevel] = []
    failures: list[str] = []
    for size in element_sizes_mm:
        try:
            mesh, value = sample(size)
        except (MeshError, SolverError, ValueError) as exc:
            failures.append(f"element_size_mm={size:g}: {exc}")
            continue
        levels.append(_level_from(mesh, element_size_mm=size, value=value))
    return assess(
        quantity,
        unit,
        levels,
        gci_threshold=gci_threshold,
        formal_order=formal_order,
        failures=failures,
    )


__all__ = [
    "DEFAULT_GCI_THRESHOLD",
    "MAXIMUM_CREDIBLE_ORDER",
    "MEASURE_UNIT",
    "MINIMUM_REFINEMENT_RATIO",
    "NEGLIGIBLE_CHANGE",
    "RECOMMENDED_REFINEMENT_RATIO",
    "SAFETY_FACTOR",
    "ConvergenceStudy",
    "GridLevel",
    "PlaneMesh",
    "Sampler",
    "SolidMesh",
    "StudyMesh",
    "UnconvergedError",
    "Verdict",
    "assess",
    "observed_order",
    "run_study",
]
