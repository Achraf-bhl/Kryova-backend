"""Turn a `LoadCaseSketch` -- words and numbers -- into the solver's `LoadCase`.

The split this module exists for: the model chooses *which* face and *how
much*, in a vocabulary of six words and a material slug, and Python builds the
selectors, the material record and the load objects. Nothing the model can say
produces an invalid shape, so the pydantic failures that cost ladder prompt H4
three rounds and five and a half minutes (2026-09-06) have nowhere to occur.

`realise` is deterministic and total over a valid sketch except for one case
it refuses in words: a load with neither a force nor a pressure has no
magnitude, and a load case whose every load is like that has nothing to solve.
"""

from __future__ import annotations

from typing import Final, Literal

from app.ai.schemas import AppliedLoad, FaceName, LoadCaseDraft, LoadCaseSketch
from app.solve.materials import MATERIALS
from app.solve.types import (
    FaceSelector,
    Fixture,
    ForceLoad,
    GravityLoad,
    Load,
    LoadCase,
    PressureLoad,
)

Axis = Literal["x", "y", "z"]
Side = Literal["min", "max"]

#: The one convention that turns a word into a selector. +Z up, +X right, +Y
#: away from the viewer -- the same frame the parsing prompt states, so "the
#: top" the model was told about and "the top" it is given here agree.
FACE_SELECTORS: Final[dict[str, tuple[Axis, Side]]] = {
    "top": ("z", "max"),
    "bottom": ("z", "min"),
    "right": ("x", "max"),
    "left": ("x", "min"),
    "back": ("y", "max"),
    "front": ("y", "min"),
}


class SketchProblem(ValueError):
    """A sketch that cannot become a load case, said in words the model can act on."""


def face_selector(face: FaceName) -> FaceSelector:
    axis, side = FACE_SELECTORS[face]
    return FaceSelector(axis=axis, side=side)


def _fixture(face: FaceName, kind: str) -> Fixture:
    axis, _ = FACE_SELECTORS[face]
    if kind == "clamp":
        return Fixture(where=face_selector(face), kind="clamp", name=f"{face} face held")
    # A roller or a symmetry plane is normal to the face it sits on -- that is
    # the only reading of "a roller on the bottom" that holds the part up.
    return Fixture(
        where=face_selector(face),
        kind="roller" if kind == "roller" else "symmetry",
        normal=axis,
        name=f"{kind} on the {face} face",
    )


def _load(applied: AppliedLoad) -> Load | None:
    """The solver's load for one applied load, or None when it has no magnitude."""
    if len(applied.force_n) == 3 and any(component != 0.0 for component in applied.force_n):
        x, y, z = (float(component) for component in applied.force_n)
        return ForceLoad(
            where=face_selector(applied.face),
            force_n=(x, y, z),
            name=f"force on the {applied.face} face",
        )
    if applied.pressure_mpa != 0.0:
        return PressureLoad(
            where=face_selector(applied.face),
            pressure_mpa=float(applied.pressure_mpa),
            name=f"pressure on the {applied.face} face",
        )
    return None


def realise(sketch: LoadCaseSketch) -> LoadCaseDraft:
    """The solver's load case for a sketch, with the sketch's caveats carried over.

    A load the sketch named without a magnitude is reported in `unresolved`
    rather than dropped silently -- the engineer asked for it and must see
    that it is not in the run. If nothing with a magnitude remains the sketch
    is refused, because a load case with no load is not a draft, it is a
    question.
    """
    unresolved = list(sketch.unresolved)
    loads: list[Load] = []
    for applied in sketch.loads:
        load = _load(applied)
        if load is None:
            unresolved.append(
                f"The load on the {applied.face} face has no magnitude -- give it a "
                "force in newtons or a pressure in MPa."
            )
            continue
        loads.append(load)
    if sketch.self_weight:
        loads.append(GravityLoad(name="self weight"))
    if not loads:
        raise SketchProblem(
            "No load in the description has a magnitude. Say how much force, in "
            "newtons, or what pressure, in MPa, and on which face."
        )

    fixtures = [_fixture(support.face, support.kind) for support in sketch.supports]
    material = MATERIALS[sketch.material]

    case = LoadCase(
        name=sketch.name.strip() or "Load case",
        material=material,
        fixtures=fixtures,
        loads=loads,
    )
    assumptions = list(sketch.assumptions)
    # The face convention is a choice the engineer did not make and must be
    # able to check, so it travels with every draft rather than living only
    # in a prompt they never see.
    assumptions.append(
        "Faces are named on the part's bounding box with +Z up, +X to the right "
        "and +Y away from the viewer."
    )
    return LoadCaseDraft(load_case=case, assumptions=assumptions, unresolved=unresolved)
