"""Named, reusable load cases, composed from the vocabulary in `types.py`.

Phase 12.1. The problem it exists for: a load case is invented per conversation
today, so no two runs are comparable and nothing can be reviewed against what
was actually applied. A recipe here is a *named* set of loads with a source for
its factors, so a result can say "this is the 1.5 ultimate case" and mean the
same thing next month.

**This composes the existing vocabulary; it does not replace it.** Decision 2
names `loads.py` and `selection.py` as the real asset, and every function below
returns ordinary `Load` objects that `assemble_loads` already knows how to turn
into nodal forces. There is no second load type, no parallel selector, and
nothing here reaches into the solver.

Units are mm-N-MPa throughout: forces N, pressures MPa, moments N·mm,
accelerations mm/s², rotation rpm.

## What the existing vocabulary cannot express

Reported rather than worked around, because the fix belongs in `types.py` and
`selection.py` and adding a parallel vocabulary here would be worse than the gap:

* **No annular selector.** A bolt head bears on a ring between its bearing
  diameter and the clearance hole, and no selector describes a ring. `CylinderSelector`
  picks a bore's wall, `BoxSelector` picks the material around it. So
  `bolt_preload` takes the two bearing regions as caller-supplied selectors
  rather than deriving them, and an annular selector would remove that argument.
* **No pretension element.** `LoadCase` has no equivalent of CalculiX's
  `*PRE-TENSION SECTION`, so a preload can only be applied to the clamped member
  as a self-equilibrating force pair. That is statically correct and it is not a
  joint analysis: the bolt's own stiffness, the load introduction factor and
  separation of the members are all outside it.
* **No contact.** Clamped members cannot separate, so a preloaded joint stays
  linear however hard the external load pulls. A separated joint is exactly the
  case a preload calculation exists to prevent, and this cannot see it.
* ~~**A `LoadCase` records no provenance.**~~ **Closed 2026-09-10.**
  `LoadCase.provenance` exists in `types.py` and `compose(..., recipes=(...),
  factor=...)` fills it, so "this is the 1.5 ultimate case" is a record rather
  than a claim in a string. A case built by hand leaves it `None`, which means
  *hand-authored* and never *unknown* — stamping a recipe onto a case nobody
  derived that way would be a citation for work that did not happen.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.solve.materials import Source, SourceKind
from app.solve.types import (
    STANDARD_GRAVITY_MM_S2,
    BearingLoad,
    CentrifugalLoad,
    Fixture,
    ForceLoad,
    GravityLoad,
    Load,
    LoadCase,
    Material,
    MomentLoad,
    PressureLoad,
    Selector,
)

# --- sources for the factors ------------------------------------------------

_AIRWORTHINESS = Source(
    citation="CS-25.303 / 14 CFR 25.303 — Factor of safety",
    kind=SourceKind.STANDARD,
    note=(
        "Ultimate load is 1.5 x limit load unless otherwise specified. The number is "
        "the airworthiness convention; a machine designed to another code (EN 13001, "
        "ASME BTH, a customer specification) uses that code's factors instead."
    ),
)
_STATICS = Source(
    citation="Statics: a preload is a self-equilibrating internal force pair",
    kind=SourceKind.DERIVED,
)
_PRACTICE = Source(
    citation="General mechanical design practice; the factor is the caller's to justify",
    kind=SourceKind.DERIVED,
)

#: The airworthiness factor between limit and ultimate load. Named rather than
#: typed as 1.5 at each call site, so changing code means changing one constant
#: and finding every case that assumed it.
ULTIMATE_FACTOR: Final = 1.5


@dataclass(frozen=True)
class LoadRecipe:
    """What a named load case is, and where its numbers come from.

    Carried beside the loads rather than inside them: a `Load` is what the
    solver assembles and must stay exactly that, while this is what a reviewer
    reads. `describe()` is what a job row should store.
    """

    key: str
    title: str
    summary: str
    source: Source
    #: What this case does *not* represent. Every one of these has been the
    #: reason a plausible analysis was wrong.
    caveats: tuple[str, ...] = ()

    def describe(self, **applied: object) -> dict[str, object]:
        """The provenance record for a run: the recipe, its source, its arguments."""
        return {
            "recipe": self.key,
            "title": self.title,
            "summary": self.summary,
            "source": self.source.to_dict(),
            "caveats": list(self.caveats),
            "applied": dict(applied),
        }


RECIPES: dict[str, LoadRecipe] = {}


def _register(recipe: LoadRecipe) -> LoadRecipe:
    if recipe.key in RECIPES:
        raise ValueError(
            f"Two load recipes share the key {recipe.key!r}. A recipe key is how a "
            f"stored result names the case it ran; it must identify one."
        )
    RECIPES[recipe.key] = recipe
    return recipe


def describe(key: str) -> LoadRecipe:
    """The recipe by key, or a refusal naming the ones that exist."""
    recipe = RECIPES.get(key)
    if recipe is None:
        raise KeyError(
            f"{key!r} is not a load recipe. Available: {', '.join(sorted(RECIPES))}."
        )
    return recipe


# --- the recipes ------------------------------------------------------------

SELF_WEIGHT = _register(
    LoadRecipe(
        key="self-weight",
        title="Self weight",
        summary="The part holding itself up under standard gravity.",
        source=Source(
            citation="Standard gravity, 9.80665 m/s2 (CGPM 1901)", kind=SourceKind.STANDARD
        ),
        caveats=(
            "Static only: a part that is accelerated, dropped or shaken sees more than 1 g.",
        ),
    )
)

INERTIAL = _register(
    LoadRecipe(
        key="inertial",
        title="Inertial load in g",
        summary="A uniform acceleration of the whole body, stated as a multiple of g.",
        source=_PRACTICE,
        caveats=(
            "A quasi-static substitute for a dynamic event. It is right when the event "
            "is slow against the part's first natural frequency and misleading when it "
            "is not — check the modal result before trusting a g load.",
        ),
    )
)

PRESSURE = _register(
    LoadRecipe(
        key="pressure",
        title="Surface pressure",
        summary="A uniform gauge pressure acting along the surface's own outward normal.",
        source=_PRACTICE,
        caveats=(
            "Gauge, not absolute: what is applied is the difference across the wall.",
            "Pressure scales with the area it acts on, unlike a total force. Resizing "
            "the face changes the load, which is usually what is meant.",
        ),
    )
)

SPIN = _register(
    LoadRecipe(
        key="spin",
        title="Rotation",
        summary="Centrifugal body load at a stated speed, with an optional overspeed factor.",
        source=_PRACTICE,
        caveats=(
            "Quadratic in speed: a 10% overspeed is a 21% load, and doubling the rpm "
            "quadruples it.",
            "The body load only. Blade or rim loads transferred into a hub are separate.",
        ),
    )
)

BOLT_PRELOAD = _register(
    LoadRecipe(
        key="bolt-preload",
        title="Bolt preload on the clamped member",
        summary=(
            "A tightened fastener's clamping action, as an equal and opposite force pair "
            "on the two bearing faces of the stack."
        ),
        source=_STATICS,
        caveats=(
            "The clamped member only. The bolt itself is not modelled, so it carries no "
            "stress here and its stiffness does not share the external load.",
            "Nothing can separate: with no contact, the joint stays linear however hard "
            "the external load pulls, which is the failure a preload exists to prevent.",
            "The preload magnitude is an assembly estimate, not a measurement — see "
            "app.parts.fasteners.assembly_preload for how much of it is a friction guess.",
        ),
    )
)

FACTORED = _register(
    LoadRecipe(
        key="factored",
        title="Factored load set",
        summary="An existing set of loads multiplied by a stated factor of safety.",
        source=_AIRWORTHINESS,
        caveats=(
            "A linear scaling. It is exact for a linear-static run and meaningless for "
            "anything with contact, plasticity or large deflection in it.",
        ),
    )
)


# --- builders ---------------------------------------------------------------


def self_weight(
    direction: tuple[float, float, float] = (0.0, 0.0, -1.0), *, name: str = "Self weight"
) -> list[Load]:
    """The part under its own weight. `direction` need not be normalised."""
    return [GravityLoad(direction=direction, name=name)]


def inertial(
    direction: tuple[float, float, float],
    g: float,
    *,
    name: str | None = None,
) -> list[Load]:
    """A quasi-static acceleration of `g` times standard gravity, along `direction`.

    This is how a pothole strike, a curb drop or a braking case is posed for a
    static solve. `g` is a multiple of standard gravity, so 3.0 is a 3 g case;
    it must be positive and the direction carries the sign.
    """
    if g <= 0.0:
        raise ValueError(
            f"An inertial load of {g} g has no magnitude. Give a positive multiple of "
            f"gravity and put the sign in `direction` — a 3 g downward case is "
            f"g=3.0 with direction=(0, 0, -1)."
        )
    return [
        GravityLoad(
            direction=direction,
            magnitude_mm_s2=g * STANDARD_GRAVITY_MM_S2,
            name=name or f"{g:g} g inertial",
        )
    ]


def pressure(where: Selector, gauge_mpa: float, *, name: str = "Pressure") -> list[Load]:
    """A uniform gauge pressure on a surface. Positive pushes into the material.

    A vacuum is a negative gauge pressure of at most one atmosphere: -0.101325
    MPa is a perfect vacuum on the outside of a vessel, and nothing on Earth
    pulls harder than that, which is the sanity check this refuses without.
    """
    if gauge_mpa < -0.101325:
        raise ValueError(
            f"A gauge pressure of {gauge_mpa} MPa is more suction than a perfect vacuum "
            f"(-0.101325 MPa at sea level). If this is an absolute pressure, subtract "
            f"atmospheric from it before applying it."
        )
    return [PressureLoad(where=where, pressure_mpa=gauge_mpa, name=name)]


def spin(
    axis_point: tuple[float, float, float],
    axis_direction: tuple[float, float, float],
    rpm: float,
    *,
    overspeed: float = 1.0,
    name: str | None = None,
) -> list[Load]:
    """Rotation about an axis, optionally at an overspeed factor on the rated rpm.

    The factor multiplies the *speed*, so the load rises with its square: an
    overspeed of 1.1 is a 21% higher body force. Stating it as a speed factor is
    deliberate — that is how a rated overspeed is specified, and converting it
    to a load factor by hand is where the square gets lost.
    """
    if overspeed <= 0.0:
        raise ValueError(
            f"An overspeed factor of {overspeed} is not a speed. It multiplies the rated "
            f"rpm, so 1.0 is the rated speed and 1.1 is 10% above it."
        )
    return [
        CentrifugalLoad(
            axis_point=axis_point,
            axis_direction=axis_direction,
            rpm=rpm * overspeed,
            name=name or f"{rpm * overspeed:g} rpm",
        )
    ]


def bolt_preload(
    *,
    under_head: Selector,
    under_nut: Selector,
    axis: tuple[float, float, float],
    preload_n: float,
    name: str = "Bolt preload",
) -> list[Load]:
    """A tightened bolt's clamping action on the member between its bearing faces.

    Returns two `ForceLoad`s: `preload_n` along `axis` on the region under the
    head, and the same magnitude back along it under the nut. `axis` points from
    the head toward the nut, so both forces push into the stack and the pair
    sums to zero — a preload is internal to the joint and must not accelerate
    the model.

    `preload_n` is a number somebody has to justify. `app.parts.fasteners.assembly_preload`
    derives it from the fastener's property class and a stated friction
    coefficient, and reports it as an estimate, which it is.

    Read `BOLT_PRELOAD.caveats` before believing a result from this: the bolt is
    not in the model, and nothing can separate.
    """
    if preload_n <= 0.0:
        raise ValueError(
            f"A preload of {preload_n} N is not a preload. It is the tension left in the "
            f"bolt after tightening, and it is positive; a joint with none is a joint "
            f"with no preload case to run."
        )
    magnitude = float(math.sqrt(sum(component**2 for component in axis)))
    if magnitude <= 0.0:
        raise ValueError(
            "A bolt axis of (0, 0, 0) has no direction. Give the direction from the head "
            "toward the nut — for a bolt down the z axis that is (0, 0, -1)."
        )
    unit = tuple(component / magnitude for component in axis)
    head_force = tuple(preload_n * component for component in unit)
    # `+ 0.0` normalises the negative zero that negating a zero component leaves
    # behind; -0.0 compares equal to 0.0 but reads as a mistake in a report.
    nut_force = tuple(-value + 0.0 for value in head_force)
    return [
        ForceLoad(
            where=under_head,
            force_n=(head_force[0], head_force[1], head_force[2]),
            name=f"{name} (under head)",
        ),
        ForceLoad(
            where=under_nut,
            force_n=(nut_force[0], nut_force[1], nut_force[2]),
            name=f"{name} (under nut)",
        ),
    ]


def scaled(loads: Iterable[Load], factor: float) -> list[Load]:
    """Every load multiplied so the *force* it applies scales by `factor`.

    **A centrifugal load scales by the square root of the factor, not by it.**
    The body force is rho.omega^2.r, so multiplying the rpm by `factor` would
    multiply the load by `factor` squared — a 1.5 ultimate case would come out
    2.25 times the limit load. The speed is scaled by sqrt(factor) instead, and
    that is the whole reason this function exists rather than a comprehension at
    each call site.

    A `Load`'s selectors, directions and names are otherwise untouched; the
    returned loads are new objects and the originals are unchanged.
    """
    if factor <= 0.0:
        raise ValueError(
            f"A load factor of {factor} is not a factor. Use a positive multiplier and "
            f"reverse the load's own direction if the case is meant to act the other way."
        )

    out: list[Load] = []
    for load in loads:
        if isinstance(load, ForceLoad | BearingLoad):
            out.append(
                load.model_copy(
                    update={"force_n": tuple(factor * c for c in load.force_n)}
                )
            )
        elif isinstance(load, PressureLoad):
            out.append(load.model_copy(update={"pressure_mpa": factor * load.pressure_mpa}))
        elif isinstance(load, MomentLoad):
            out.append(
                load.model_copy(
                    update={"moment_n_mm": tuple(factor * c for c in load.moment_n_mm)}
                )
            )
        elif isinstance(load, GravityLoad):
            out.append(
                load.model_copy(update={"magnitude_mm_s2": factor * load.magnitude_mm_s2})
            )
        elif isinstance(load, CentrifugalLoad):
            out.append(load.model_copy(update={"rpm": math.sqrt(factor) * load.rpm}))
        else:  # pragma: no cover - the discriminated union makes this unreachable
            raise TypeError(
                f"{type(load).__name__} is a load type `scaled` has not been taught to "
                f"factor. Add it here rather than scaling it at the call site, so the "
                f"square on a speed-driven load cannot be forgotten again."
            )
    return out


def ultimate(loads: Iterable[Load], *, factor: float = ULTIMATE_FACTOR) -> list[Load]:
    """The limit loads factored to ultimate. 1.5 by airworthiness convention.

    See `FACTORED.source` for where the number comes from, and change it
    deliberately: a machine built to EN 13001 or ASME BTH uses that code's
    factors, and inheriting 1.5 because it was the default is not a decision.
    """
    return scaled(loads, factor)


def compose(
    name: str,
    material: Material,
    fixtures: Sequence[Fixture],
    *load_groups: Iterable[Load],
    delta_t_k: float | None = None,
    recipes: Sequence[str] = (),
    factor: float | None = None,
) -> LoadCase:
    """Build a `LoadCase` from a material, fixtures and one or more recipe outputs.

    Concatenates the groups in order, so a case is written the way it is
    described: `compose("Lift", steel, [clamp], self_weight(), pressure(...))`.

    **`recipes` and `factor` become the case's provenance** (E12.1). This
    module's docstring named the gap they close: a `LoadCase` carried `name` and
    nothing that said which recipe and which factor produced its loads, so
    "this is the 1.5 ultimate case" was a claim in a string. `LoadCase` has a
    `provenance` field since 2026-09-10 and this fills it.

    Passing no recipes leaves `provenance` **None**, and that is correct rather
    than lazy: a case assembled by hand has no recipe behind it, and stamping
    one would be a citation for a derivation nobody performed.
    """
    loads: list[Load] = [load for group in load_groups for load in group]
    if not loads:
        raise ValueError(
            f"Load case {name!r} has no loads. A static solve with none is a solve of "
            f"nothing; give it at least one recipe's output."
        )
    unknown = [key for key in recipes if key not in RECIPES]
    if unknown:
        # Refused rather than recorded: a provenance naming a recipe that does
        # not exist is worse than none, because it reads as checkable and is not.
        raise ValueError(
            f"Load case {name!r} claims recipes that are not in the library: "
            f"{', '.join(sorted(unknown))}. Known recipes: {', '.join(sorted(RECIPES))}."
        )
    provenance: dict[str, object] | None = None
    if recipes:
        provenance = {
            "recipes": [describe(key).describe() for key in recipes],
            "library": "app.solve.load_library",
        }
        if factor is not None:
            provenance["factor"] = factor
    return LoadCase(
        name=name,
        material=material,
        fixtures=list(fixtures),
        loads=loads,
        delta_t_k=delta_t_k,
        provenance=provenance,
    )


def catalogue() -> Mapping[str, LoadRecipe]:
    """Every recipe, for a caller that wants to list what is available."""
    return dict(sorted(RECIPES.items()))
