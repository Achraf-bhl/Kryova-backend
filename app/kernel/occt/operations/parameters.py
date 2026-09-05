"""Named parameters on the open kernel: `catia_list_parameters`, `catia_set_parameter`.

**Why these were missing, and what it cost.** The system prompt tells the model
*"Dimensions are parameters. Prefer catia_set_parameter over rebuilding a
feature"* — and on `GEOMETRY_BACKEND=occt` that tool did not exist. Measured end
to end on 2026-09-05, rung 3 of the ladder ("the plate has to weigh 2.4 kg —
adjust the thickness until the measured mass is within 20 grams"): the model
built the plate, measured it, and then had no way to change a dimension. So it
did the only other thing available and padded the same sketch again, four times,
at four different lengths. Every call returned `ok`. The part ended as seven
stacked pads weighing 2.958 kg, and the model reported that as the answer.

Without this module the whole of rung 3 is unreachable, and with it `correct.py`,
`sensitivity.py` and 5.1's mass budget — every one of which is a loop that
changes a dimension and measures again.

**A part built in conversation has no parameter set, so its build log is one.**
CATIA holds `Parameters` and Kryova asks it; OCCT holds a shape and no history at
all. What it does have is the ordered list of calls that produced the shape, and
that list is exactly the "specification that is compiled" `app/design/` is built
around — applied to a part the agent assembled call by call rather than one
compiled from a spec. So every numeric argument of every mutating call is a
parameter, addressed as `Pad.1\\length_mm`, and setting one **rewrites the call
and replays the whole part**.

Three consequences that are the point rather than side effects:

* **Regeneration is a rebuild from the top, so there is no topological naming
  problem to solve.** This is the same argument `app/design/spec.py` makes: a
  spec that is recompiled has no downstream edit to shatter. Replay allocates the
  same names in the same order, so `Pad.1` is still `Pad.1` afterwards.
* **A value that will not build leaves the part exactly as it was.** The replay
  runs into a *fresh* document and is swapped in only once it has finished, so a
  fillet radius the geometry cannot carry comes back as a refusal with the
  reason, not as a part that has lost its fillet.
* **The old part is measurable throughout.** Nothing is mutated in place, so a
  failed set cannot leave a half-rebuilt document behind — which is the state an
  agent cannot recover from because nothing tells it what is missing.

The unit argument is honoured for the reason CATIA's own tool gives: a parameter
is typed, and setting a length in degrees is a silent no-op. Here the unit is
inferred from the argument's own suffix — `length_mm` is millimetres and
`angle_deg` is degrees — which is the same convention the whole registry is
written in and so cannot drift from it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import GeometryError
from app.kernel.occt.operations.context import BuildContext

LIST = "catia_list_parameters"
SET = "catia_set_parameter"

#: Any run of these separates a feature from one of its dimensions. CATIA writes
#: `Pad.1\FirstLimit\Length`; a model that has never seen a seat writes a slash;
#: a model reading a JSON payload writes a doubled backslash, because that is what
#: the payload showed it. All of them mean the same thing and all of them are
#: accepted — see `_canonical`.
_SEPARATOR_RUN: Final = re.compile(r"[\\/.]+")

#: Argument suffix → the unit that argument is in. The registry names every
#: quantity with its unit (`length_mm`, `angle_deg`, `mass_kg`), so this reads the
#: convention rather than restating it; a new argument that follows the naming
#: rule needs no entry here.
_UNIT_BY_SUFFIX: Final[tuple[tuple[str, str], ...]] = (
    ("_mm3", "mm3"),
    ("_mm2", "mm2"),
    ("_mm", "mm"),
    ("_deg", "deg"),
    ("_kg", "kg"),
    ("_mpa", "MPa"),
    ("_n", "N"),
    ("_s", "s"),
)

#: Arguments that are numbers and are not dimensions. A count of sides or teeth
#: is an integer that changes what the feature *is*, not how big it is, and
#: offering it as a length-valued parameter invites a model to set 6.5 sides.
_NOT_A_DIMENSION: Final = frozenset({"sides", "teeth", "count", "count_x", "count_y"})


def unit_of(argument: str) -> str:
    """The unit an argument is in, from its own name. `""` when it has none."""
    lowered = argument.lower()
    for suffix, unit in _UNIT_BY_SUFFIX:
        if lowered.endswith(suffix):
            return unit
    return ""


def list_parameters(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Every dimension of every feature this part is built from.

    Named `<feature>\\<argument>` because that is the shape CATIA's own names have
    and the agent should not have to learn two spellings of the same idea. The
    filter is a plain substring, case-folded, matching the declared tool.
    """
    document = context.require_document()
    wanted = str(arguments.get("filter") or "").strip().lower()

    found = []
    for entry in context.journal:
        for name, value in _dimensions_of(entry):
            if wanted and wanted not in name.lower():
                continue
            found.append(
                {"name": name, "value": value, "unit": unit_of(name.split("\\")[-1])}
            )

    return {
        "document": document.name,
        "parameters": found,
        "count": len(found),
        "note": (
            "These are the dimensions this part was built from. Change one with "
            "catia_set_parameter and the part is rebuilt from the top, so every "
            "feature that depends on it moves with it."
        ),
    }


def set_parameter(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Change one dimension and rebuild the part from the top."""
    context.require_document()  # refuses with the reason when nothing is open
    name = str(arguments.get("name") or "").strip()
    if not name:
        raise GeometryError(
            f"{SET} needs the name of a parameter. Call {LIST} to see them; they are "
            "written like 'Pad.1\\\\length_mm'."
        )

    index, argument, previous = _locate(context, name)
    value = _as_number(arguments.get("value"), name)
    _check_unit(arguments.get("unit"), argument, name)

    rebuilt = _replay(context, index=index, argument=argument, value=value)

    # Only now, with a complete part in hand, is the old one let go of.
    context.document = rebuilt.document
    context.journal = rebuilt.journal

    return {
        "parameter": {"name": name, "value": value, "unit": unit_of(argument)},
        "previous_value": previous,
        "features": rebuilt.document.feature_names() if rebuilt.document else [],
        **(rebuilt.document.measure(detail=context.detail) if rebuilt.document else {}),
    }


# -- the journal -------------------------------------------------------------


def _dimensions_of(entry: Any) -> list[tuple[str, float]]:
    """The `(name, value)` pairs one recorded call contributes."""
    owner = entry.feature or entry.tool.removeprefix("catia_")
    out: list[tuple[str, float]] = []
    for key, value in entry.arguments.items():
        if key in _NOT_A_DIMENSION or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append((f"{owner}\\{key}", float(value)))
    return out


def _canonical(text: str) -> str:
    r"""One spelling of a parameter name that every real spelling maps onto.

    **A backslash cannot survive the round trip to a model and back.** The tool
    payload is JSON, so `Pad.1\length_mm` is *shown* to the model as
    `Pad.1\\length_mm`, and it types back exactly what it read — a literal
    double backslash. Measured on 2026-09-05: rung 3's agent called
    `catia_list_parameters`, copied the name it was given, and had all four of its
    `catia_set_parameter` calls refused. It then gave up on the parameter loop
    entirely and padded a second slab over the part instead, which reached the
    target mass with the bore and the holes filled in.

    So every separator is the same separator here: runs of backslash, forward
    slash and dot all fold to one, and the comparison is case-folded. A feature
    name contains a dot, which is why the *whole* name is normalised rather than
    split — `Pad.1\length_mm` and `Pad.1.length_mm` and `pad.1//length_mm` are
    one key, and no spelling a model can produce from what it was shown is
    refused for punctuation.
    """
    folded = _SEPARATOR_RUN.sub("|", text.strip().lower())
    return folded.strip("|")


def _table(context: BuildContext) -> list[tuple[int, str, str, float]]:
    """Every settable dimension as `(journal index, owner, argument, value)`."""
    out: list[tuple[int, str, str, float]] = []
    for index, entry in enumerate(context.journal):
        owner = entry.feature or entry.tool.removeprefix("catia_")
        for key, value in entry.arguments.items():
            if key in _NOT_A_DIMENSION or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                out.append((index, owner, key, float(value)))
    return out


def _locate(context: BuildContext, name: str) -> tuple[int, str, float]:
    """Which recorded call owns this parameter, and what it is set to now."""
    table = _table(context)
    wanted = _canonical(name)

    for index, owner, argument, value in table:
        if _canonical(f"{owner}|{argument}") == wanted:
            return index, argument, value

    # A bare dimension name, when only one feature has one by that name. An agent
    # that has lost track of the prefix is not wrong about what it wants, and
    # refusing an unambiguous request teaches it to stop using the tool.
    bare = [item for item in table if _canonical(item[2]) == wanted]
    if len(bare) == 1:
        index, _, argument, value = bare[0]
        return index, argument, value

    separator = chr(92)
    listed = ", ".join(
        f"{owner}{separator}{argument}" for _, owner, argument, _ in table
    )
    raise GeometryError(
        f"No parameter named {name!r} in this part. This part's parameters are: "
        f"{listed or 'none — this part has no dimensions yet'}. Call {LIST} for "
        "their current values."
    )


def _as_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise GeometryError(
            f"{name} is a dimension and needs a number; got {value!r}."
        )
    try:
        return float(value)
    except (TypeError, ValueError) as bad:
        raise GeometryError(
            f"{name} is a dimension and needs a number; got {value!r}."
        ) from bad


def _check_unit(given: Any, argument: str, name: str) -> None:
    """A wrong unit is refused, never quietly accepted.

    CATIA's own message for this says why: a parameter is typed and setting a
    length in degrees does nothing, leaving the model looking unchanged with no
    error to explain it. The same has to be true here or the two backends answer
    differently to the same call.
    """
    if given is None:
        return
    expected = unit_of(argument)
    if str(given) != expected:
        shown = expected or "no unit"
        raise GeometryError(
            f"Parameter {name!r} is in {shown}, not {given!r}. A dimension is typed, "
            "and setting the wrong unit is the kind of no-op that leaves a part "
            "looking unchanged with nothing to explain why."
        )


def _replay(context: BuildContext, *, index: int, argument: str, value: float) -> Any:
    """Rebuild the whole part with one argument changed, into a fresh document.

    Into a fresh one, and swapped in by the caller only on success — so a value
    the geometry cannot carry costs a refusal and nothing else. Rebuilding in
    place would leave a part half-regenerated, which is the state an agent cannot
    recover from because nothing tells it what is now missing.
    """
    from app.kernel.occt.operations import HANDLERS

    rebuilt = BuildContext(detail=context.detail)
    for position, entry in enumerate(context.journal):
        replacement = dict(entry.arguments)
        if position == index:
            replacement[argument] = value
        handler = HANDLERS.get(entry.tool)
        if handler is None:  # pragma: no cover - the journal only holds handled tools
            continue
        try:
            # Recorded here rather than by the runner, because a replay calls the
            # handlers directly — there is no runner in this loop. Every entry in
            # the journal is by definition a recorded tool, so there is nothing to
            # test for.
            rebuilt.record(entry.tool, replacement, handler(rebuilt, replacement))
        except GeometryError as exc:
            if position == index:
                raise GeometryError(
                    f"{value} does not build: {exc} The part is unchanged — nothing "
                    "was rebuilt, so it is still exactly as it was before this call."
                ) from exc
            raise GeometryError(
                f"Rebuilding with {argument}={value} failed further down the part, at "
                f"{entry.tool}: {exc} The part is unchanged. A later feature depends on "
                "this dimension and cannot carry the new value."
            ) from exc
    return rebuilt


__all__ = ["LIST", "SET", "list_parameters", "set_parameter", "unit_of"]
