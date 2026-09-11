"""Which of the stated requirements a turn's own measurements actually confirm.

`planning.py` holds *what was asked for*; `resume.py` holds *what was done*.
Neither answers the question that decides whether a turn may close: **was what
was asked for measured, and did it come out right?** This is that half, and it
is master plan 16.2's checking step.

It exists because of a failure that has now been recorded four times on the
seat, always with the same shape. 2026-09-06, attempt 3: six stated
requirements, three built, and a closing report of success. Ladder prompt S1:
"make it 2.4 kg", closed at 2.459 kg described as *"within 20 grams"*, which is
59 grams out. Ladder prompt S2, run 6, the same evening: the clash check failed
with an error the agent did not understand, it invented a story about needing an
FEA analysis first, and closed the turn reporting the assembly's mass instead --
with the interference, the one thing the user asked about, never checked. In
every case most tool calls returned `ok`. Nothing was comparing the answer to
the question, so nothing could notice.

**Three states, never two.** An objective is `confirmed` only when a number this
conversation actually measured matches the number the engineer stated. It is
`missed` only when a measurement of that kind exists and contradicts them.
Everything else is outstanding -- including, and especially, everything nobody
looked at. This is `assertions.py`'s rule for an unmeasured assertion and
`VisualReview`'s rule for a check that could not run, applied to a requirement.

**This is the one module in the codebase that converts a unit, and it is
sanctioned.** `planning.py` records "2.4 kg" and "within 20 grams" exactly as
written and says in as many words that making them comparable is the consumer's
job. The engineering pipeline's mm-N-MPa rule is about quantities entering the
system at its boundary; this compares two numbers a human wrote and a machine
measured, and refusing to convert here would mean a target in grams could never
be checked against a mass in kilograms -- which is the S1 defect exactly.

**Nothing here is keyed to a tool name.** Measurements are collected by walking
whatever a tool returned and keeping the numbers whose *key* says what they are
(`mass_kg`, `volume_mm3`, `bounding_box_mm`, `length_mm`, ...). A tool added
tomorrow that reports `bore_mm` is read with no change here, and a tool that
reports nothing numeric contributes nothing. A list of tools to look inside is a
list that is wrong the next time one is added.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Final, Iterable, Sequence

from app.ai.planning import Objective, Plan, Quantity

#: How a stated unit converts to the unit measurements are reported in. Length
#: is reported in mm and mass in kg, everywhere -- that is the codebase's own
#: convention and there is nothing to negotiate. Only the *engineer's* spelling
#: varies, so only that side needs a factor.
_TO_CANONICAL: Final[dict[str, float]] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "thou": 0.0254,
    "kg": 1.0,
    "kgs": 1.0,
    "g": 0.001,
    "gram": 0.001,
    "grams": 0.001,
    "gramme": 0.001,
    "grammes": 0.001,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "t": 1000.0,
    "deg": 1.0,
    "degree": 1.0,
    "degrees": 1.0,
}

#: What a result key means, matched on the key's own suffix rather than on a
#: table of key names. `mass_kg`, `part_mass_kg` and `component_mass_kg` are all
#: masses in kilograms and none of them has to be enumerated.
_KEY_KIND: Final[tuple[tuple[str, str], ...]] = (
    ("_kg", "mass"),
    ("_mm", "length"),
    ("_mm2", "area"),
    ("_mm3", "volume"),
    ("_deg", "angle"),
    ("_n", "force"),
    ("_mpa", "stress"),
)

#: A key naming a *setting* rather than a measurement. A clearance the caller
#: asked for is not a clearance that was measured, and a tolerance echoed back
#: in a payload is not evidence of anything. Matched as whole words in the key.
_NOT_A_MEASUREMENT: Final[frozenset[str]] = frozenset(
    {"requested", "target", "nominal", "tolerance", "default", "limit", "max", "min"}
)

#: Relative slack when the engineer stated no tolerance of their own. A
#: dimension a model built to 40.0 and measured at 40.0000001 is the same
#: dimension; one built to 44 is not.
DEFAULT_RELATIVE_TOLERANCE: Final[float] = 0.01

#: Absolute floor under that, so a small dimension is not held to an
#: impossible fraction of itself. 1% of 0.5 mm is 5 microns.
DEFAULT_ABSOLUTE_TOLERANCE_MM: Final[float] = 0.05


@dataclass(frozen=True, slots=True)
class Measurement:
    """One number a tool reported, with what it measures and where it came from."""

    value: float
    kind: str
    source: str

    def __str__(self) -> str:
        return f"{self.value:g} ({self.kind}, from {self.source})"


def _kind_of(key: str) -> str | None:
    """What a result key measures, or None when the key does not say.

    Suffix-matched and lower-cased. A key that does not declare its unit is not
    guessed at: an unlabelled number is exactly the thing this module must not
    treat as evidence.
    """
    lowered = key.lower()
    if set(re.split(r"[^a-z0-9]+", lowered)) & _NOT_A_MEASUREMENT:
        return None
    for suffix, kind in _KEY_KIND:
        if lowered.endswith(suffix):
            return kind
    return None


def _numbers(value: Any) -> list[float]:
    """Every finite number under a value, however it is shaped.

    Dictionaries are walked as well as lists, and that is not tidiness -- it was
    a false negative measured on ladder prompt PRO4 turn 2, 2026-09-07. The ram
    was built to 20 mm diameter and 180 mm long and measured; the answer said
    "Diameter: 20 mm, Length: 180 mm"; and the footnote underneath it said both
    were unmeasured. `catia_measure` reports

        "bounding_box_mm": {"size": [20.0, 20.0, 180.0], "max": [...], ...}

    -- a dict under the key that declares the unit. Reading only scalars and
    lists meant every length in every measurement was dropped, so no dimensional
    requirement could ever be confirmed. A footnote that cries wolf is worse
    than no footnote, which is what `test_an_answer_that_measured_everything_is_
    left_alone` says in the other direction.
    """
    if isinstance(value, bool):  # bool is an int; a flag is not a measurement
        return []
    if isinstance(value, (int, float)):
        return [float(value)] if math.isfinite(float(value)) else []
    found: list[float] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_numbers(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_numbers(item))
    return found


def measurements_in(result: Any, source: str, _depth: int = 0) -> list[Measurement]:
    """Every labelled number in one tool result, however deeply nested.

    Depth-limited rather than trusted: a tool result is JSON from a daemon and a
    cycle would hang the turn that was meant to be checking its own work.
    """
    if _depth > 6:
        return []
    found: list[Measurement] = []
    if isinstance(result, dict):
        for key, value in result.items():
            kind = _kind_of(str(key))
            if kind is not None:
                # A key that declares a unit declares it for everything under
                # it: `bounding_box_mm` is millimetres in its `size`, its `max`
                # and its `min` alike. Descending without carrying the kind
                # down is how those inner keys -- which name a corner, not a
                # unit -- came to contribute nothing.
                found.extend(
                    Measurement(number, kind, source) for number in _numbers(value)
                )
            # Deliberately not `continue`. Walking a kind-declaring value again
            # can only *add* a reading whose own key declares a kind, and that
            # reading is the more precise of the two -- a nested `mass_kg` under
            # some future `..._mm` key would otherwise be recorded as a length
            # and as nothing else. A duplicate is harmless: matching is by
            # membership within a tolerance, not by counting. This stood as a
            # `continue` until a break check showed nothing could distinguish
            # it, which is the same reason the dead `kind == "count"` test came
            # out of `_canonical`.
            if isinstance(value, (dict, list, tuple)):
                found.extend(measurements_in(value, source, _depth + 1))
    elif isinstance(result, (list, tuple)):
        for item in result:
            found.extend(measurements_in(item, source, _depth + 1))
    return found


def _canonical(quantity: Quantity) -> float | None:
    """A stated quantity in the unit measurements are reported in, or None.

    None for a bare count: "four holes" states a number that no dimension can
    confirm, and treating it as a length would confirm a requirement against an
    unrelated 4 mm somewhere in the part. The unit lookup is the whole of that
    guard -- `planning.py` gives a quantity a unit only when the word after the
    number names one, so a count always arrives with an empty unit and finds no
    factor. A second `kind == "count"` test stood here until a break check
    showed nothing could reach it.
    """
    factor = _TO_CANONICAL.get(quantity.unit)
    if factor is None:
        return None
    return quantity.value * factor



def _is_the_tolerance(quantity: Quantity, objective: Objective) -> bool:
    """Whether this number is the clause's own slack rather than a target.

    `extract_objectives` records every number in the clause, and a tolerance is
    one of them: "2.4 kg to within 20 grams" carries 2.4 kg *and* 20 g. Read as
    a target, the 20 g demands that the part also weigh 20 grams, which nothing
    can satisfy -- so a requirement that was met came back as `missed`, which is
    the one verdict worse than saying nothing.

    Identity by value and unit, because `Objective.tolerance` is parsed from the
    same clause and is the same `Quantity`. A clause whose target and tolerance
    are genuinely the same number ("20 mm to within 20 mm") would drop its
    target here; it is not a sentence an engineer writes, and the alternative --
    matching by position in the string -- would make the record depend on where
    a regex happened to start.
    """
    slack = objective.tolerance
    return (
        slack is not None
        and quantity.value == slack.value
        and quantity.unit == slack.unit
    )


def _slack(target: float, tolerance: Quantity | None) -> float:
    """How far from `target` still counts as meeting it."""
    if tolerance is not None:
        if tolerance.kind == "percent":
            return abs(target) * tolerance.value / 100.0
        stated = _TO_CANONICAL.get(tolerance.unit)
        if stated is not None:
            return tolerance.value * stated
    return max(abs(target) * DEFAULT_RELATIVE_TOLERANCE, DEFAULT_ABSOLUTE_TOLERANCE_MM)


def _matches(target: float, tolerance: Quantity | None, values: Iterable[float]) -> bool:
    allowed = _slack(target, tolerance)
    return any(abs(value - target) <= allowed for value in values)


def assess(objectives: Sequence[Objective], measured: Sequence[Measurement]) -> Plan:
    """A `Plan` over `objectives`, marked against what was actually measured.

    Confirmed needs *every* checkable number in the clause to appear among the
    measurements. Partly-matched is not confirmed: "25.2 mm bore, 45 mm outside
    diameter, 40 mm long" with only the length measured is a bushing nobody has
    checked the bore of, and reporting it as met is the whole failure.

    `missed` is deliberately narrow. It is claimed only where a contradiction is
    real: a mass. A part has exactly one mass, so a stated mass with a measured
    mass that is outside tolerance is wrong and can be said to be wrong. A
    stated *length* that does not appear among the measured lengths is not a
    contradiction -- the part may simply not have been measured that way -- so it
    stays outstanding, which is the honest word for it.
    """
    plan = Plan(objectives=tuple(objectives))
    by_kind: dict[str, list[float]] = {}
    for measurement in measured:
        by_kind.setdefault(measurement.kind, []).append(measurement.value)

    for index, objective in enumerate(objectives):
        targets = [
            (_canonical(quantity), quantity.kind)
            for quantity in objective.quantities
            if not _is_the_tolerance(quantity, objective)
        ]
        checkable = [(value, kind) for value, kind in targets if value is not None]
        if not checkable:
            continue

        # A length may be confirmed by any length the part reports -- a bounding
        # box edge, a measured distance, a feature dimension. Volume is allowed
        # to confirm a length requirement only through its own kind, never by
        # coincidence of magnitude, which is why the kind is carried through.
        if all(
            _matches(value, objective.tolerance, by_kind.get(kind, ()))
            for value, kind in checkable
        ):
            plan.mark_confirmed(index)
            continue

        masses = [(value, kind) for value, kind in checkable if kind == "mass"]
        if masses and by_kind.get("mass"):
            if not all(
                _matches(value, objective.tolerance, by_kind["mass"]) for value, _ in masses
            ):
                plan.mark_missed(index)

    return plan


def unverified(plan: Plan) -> list[Objective]:
    """The measurable requirements nothing has confirmed, in the stated order.

    Only the measurable ones. A clause with no number -- "with a chamfer on the
    top edge" -- has to be built and cannot be checked by arithmetic, so holding
    a turn open for it would mean never closing one. Those are what the visual
    check is for.
    """
    decided = plan.confirmed | plan.missed
    return [
        objective
        for index, objective in enumerate(plan.objectives)
        if index not in decided and objective.measurable and _checkable(objective)
    ]


def _checkable(objective: Objective) -> bool:
    """Whether any of the clause's numbers carries a unit this can compare."""
    return any(_canonical(quantity) is not None for quantity in objective.quantities)


def shortfall_note(plan: Plan) -> str:
    """What to tell the model before it is allowed to call the turn finished.

    Written as an instruction with the specific clauses in it, not as a count.
    A model told "2 requirements are unverified" answers by asserting they are
    fine; a model handed the sentences it has not measured goes and measures
    them. The wrong ones are named separately and first, because a measured
    contradiction is a different job from an unchecked box.
    """
    wrong = [plan.objectives[index] for index in sorted(plan.missed)]
    open_ones = unverified(plan)
    if not wrong and not open_ones:
        return ""

    lines: list[str] = []
    if wrong:
        lines.append(
            "You are about to finish, and these requirements were measured and "
            "are WRONG:"
        )
        lines.extend(f"  - {objective.text}" for objective in wrong)
        lines.append("Correct them, or say plainly to the user that they are not met.")
    if open_ones:
        lines.append(
            "These requirements have a number in them and nothing in this "
            "conversation has measured it:"
        )
        lines.extend(f"  - {objective.text}" for objective in open_ones)
        lines.append(
            "Measure them now with the tools you have. If a measurement cannot "
            "be made, say which requirement is unverified and why -- do not "
            "describe it as done, and do not invent a reason a tool failed. "
            "Not checked is not the same as fine."
        )
    return "\n".join(lines)


def unverified_footnote(plan: Plan) -> str:
    """What the *user* is told when the turn closes with requirements unchecked.

    The model has already been given one chance to go and measure. If it closes
    anyway, the answer it wrote is very likely to describe the part as finished,
    and the user cannot see what was skipped. This appends the list to the
    answer rather than rewriting it -- the model's own words are left intact and
    the omission is stated beside them, which is the same treatment
    `stream_agent` gives a truncated answer and a tool call written as prose.
    """
    wrong = [plan.objectives[index] for index in sorted(plan.missed)]
    open_ones = unverified(plan)
    if not wrong and not open_ones:
        return ""

    lines = ["", "---", "**Not verified in this turn.**"]
    for objective in wrong:
        lines.append(f"- {objective.text} — measured and does not meet the requirement")
    for objective in open_ones:
        lines.append(f"- {objective.text} — nothing measured this")
    lines.append(
        "Anything listed here was not checked against the part, whatever the "
        "answer above says about it."
    )
    return "\n".join(lines)


#: Keys under which a solved result reports what it knows about its own
#: discretisation error. `app/simulation/runner.py` writes the first; the
#: others are what a study fills in when one was actually run.
_CONVERGENCE_KEY = "mesh_convergence"


def _unconverged_runs(results: Iterable[Any]) -> list[dict[str, Any]]:
    """Simulation results this turn read that hold no evidence about themselves.

    Takes raw tool results rather than `AgentStep`s, both because `agent.py`
    imports this module and not the other way round, and for the same reason
    `measurements_in` does: this is *what the answer was written from*, and a
    second source could disagree with it. Keyed on the shape of the payload —
    any result carrying a `mesh_convergence` block that does not say
    `converged: true` — so a tool added tomorrow that returns a solved result is
    read with no change here.
    """
    found: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        inner = result.get("result")
        payload = inner if isinstance(inner, dict) else result
        block = payload.get(_CONVERGENCE_KEY)
        if not isinstance(block, dict) or block.get("converged") is True:
            continue
        # `mesh_stats` is a sibling of `result` in what `get_simulation`
        # returns, not part of it.
        stats = result.get("mesh_stats")
        stats = stats if isinstance(stats, dict) else {}
        found.append(
            {
                "id": str(result.get("id") or payload.get("id") or "this run"),
                "basis": str(block.get("basis") or "single-grid"),
                "grids": block.get("grids"),
                "stress": payload.get("max_von_mises_mpa"),
                "displacement": payload.get("max_displacement_mm"),
                "slivers": stats.get("sliver_count"),
                "min_quality": stats.get("min_quality"),
                "elements": stats.get("element_count") or payload.get("element_count"),
            }
        )
    # One line per distinct run, in the order the turn met them.
    seen: set[str] = set()
    unique = []
    for row in found:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        unique.append(row)
    return unique


def unconverged_footnote(results: Iterable[Any]) -> str:
    """What the user is told when a turn answers from a solve with no evidence
    about its own discretisation error.

    **Measured on the seat, 2026-09-10** (`docs/verification-2026-09-10-night/`),
    and it is the reason this exists. Asked for a cantilever that "has to stay
    under 150 MPa", the product replied *"PASSES. The peak stress of 140.6 MPa
    is below your limit of 150 MPa. The bracket has approximately 9 MPa of
    margin."* The result it had just read carried
    `mesh_convergence: {converged: false, basis: "single-grid"}` and the
    sentence "treat the numbers as indicative". Neither reached the answer. The
    mesh was 808 linear tets — one element through a 10 mm thickness — and the
    peak stress was overstated by about 90%: challenged in the same
    conversation, the agent ran tet10 at 5 mm and 3.5 mm and got ~74 MPa.

    **The model already had the block and ignored it**, which is why this is a
    footnote and not a prompt rule. It appends rather than rewrites, exactly as
    `unverified_footnote` does: the model's own words stand, and what they rest
    on is stated beside them. A caveat the answer cannot omit is the only kind
    that survives a model having a bad day.

    Deliberately silent when the study *was* run and converged — then the
    evidence exists and repeating it is noise. Decision 3's rule is that an
    unconverged number is worse than no number, not that every number needs a
    disclaimer.
    """
    runs = _unconverged_runs(results)
    if not runs:
        return ""

    lines = ["", "---", "**What these numbers rest on.**"]
    slivered = False
    for run in runs:
        grids = run["grids"]
        how = f"{run['basis']}" + (f", {grids} grid" if grids == 1 else "")
        lines.append(
            f"- Run `{run['id']}` is **not converged** ({how}). Nothing here measures how "
            "much the answer would move on a finer mesh."
        )
        # A sliver is not a detail of the mesh, it is where the reported peak
        # stress comes from. Measured 2026-09-10: two runs of the same
        # cantilever, same load case, 808 and 809 elements, gave 140.6 MPa and
        # 50.4 MPa — a factor of 2.8 — and the only thing that separated them
        # was `min_quality` 0.06 against 0.49. Both were reported as a verdict
        # against the user's 150 MPa limit. The mesher has counted this since
        # it was written (`sliver_count`, shape < 0.1, "little stiffness and a
        # lot of noise to a stress recovery") and nothing had ever shown it to
        # anyone.
        if run["slivers"]:
            slivered = True
            quality = run["min_quality"]
            quality_text = f", worst {quality:.3f}" if isinstance(quality, (int, float)) else ""
            lines.append(
                f"  - and it contains **{run['slivers']} sliver element(s)**{quality_text} "
                "(a well-shaped tetrahedron scores 1.0). A peak stress read off a sliver is "
                "noise, not a stress."
            )
    lines.append(
        "A single mesh holds no evidence about its own discretisation error, however fine "
        "it looks — so any pass, fail or margin quoted above is indicative, not a verdict. "
        "Ask for a convergence study (`grids: 3`) to find out what the number really is."
    )
    if slivered:
        lines.append(
            "The sliver matters more than the grid count here: the same case re-meshed can "
            "move the peak stress by a factor of two or more while the deflection barely "
            "changes. Set `element_size_mm` so the part has several elements through its "
            "thinnest section, or ask for second-order elements."
        )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_ABSOLUTE_TOLERANCE_MM",
    "DEFAULT_RELATIVE_TOLERANCE",
    "Measurement",
    "assess",
    "measurements_in",
    "shortfall_note",
    "unconverged_footnote",
    "unverified",
    "unverified_footnote",
]
