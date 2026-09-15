"""The E22.2 case set: instructions on the mission designs, each with the edit that fulfils it.

`app/design/corruption.py` is the harness and ships no number. This is what it is run on. Every
`before` is a design the mission ladder builds and asserts against (`app/design/missions.py`),
never a fixture written for the harness, and every `reference` is built the way the mission
module itself builds a variant -- `set_parameter` where a feature reads the parameter, the
mission's own constructor where it does not. Each case was checked to be EXACT against its own
reference and to target what its name says (2026-09-15, one-off script; `tests/test_design_
corruption_cases.py` holds it).

**What the set is chosen to probe**, because a corruption rate is only as good as the edits it
asks for:

* **one parameter, one feature** -- the baseline (`m1-bore`, `m1-corners`, `m6-wall`);
* **a formula consequence** -- `m6-od` must move the bore too, because it is `od - 2·wall`, so
  an editor that leaves the bore alone is CLEAN-but-wrong rather than exact;
* **a structural edit** -- removing a feature (`m1-sharp`) and removing a repeated one
  (`m3-one-gland`), where an editor rewriting the list can renumber or drop a neighbour;
* **a whole-part change** -- the material (`m1-steel`), where every feature must stay put;
* **an edit the parameter table cannot make** -- M3's section is written from literals, and its
  `thickness_mm`, `width_mm`, `lip_mm` and `radius_mm` are read by no feature (measured
  2026-09-15). Setting the parameter alone is NO_CHANGE; the edit has to reach twenty sketch
  segments without touching the glands. That is the case the literature's whole-spec failure
  mode lives in, and it is also a defect in M3 recorded in E22.2's status, not fixed here.

**This set has not been run against a model.** A rate from it is THE QUEUE D2.
"""

from __future__ import annotations

from app.design.corruption import EditCase
from app.design.errors import SpecError
from app.design.spec import DesignSpec


def _with_material(spec: DesignSpec, material: str) -> DesignSpec:
    return DesignSpec(
        name=spec.name,
        parameters=spec.parameters,
        features=spec.features,
        material=material,
        description=spec.description,
    )


def _without(spec: DesignSpec, feature: str) -> DesignSpec:
    if feature not in spec.feature_names():
        raise SpecError(f"{spec.name} has no feature {feature}.")
    return spec.with_features([f for f in spec.features if f.name != feature])


def mission_cases() -> tuple[EditCase, ...]:
    """Every case, built fresh from the mission module each call."""
    # Imported here: `app.design.missions` reaches up into `app.assembly`, and `app/design/
    # __init__.py` defers it for the same reason.
    from app.design.missions import (
        _M3_HOLE_U_MM,
        _M6_BELT_WIDTH_MM,
        _m3_spec,
        _m6_roller_spec,
        mission,
    )

    m1 = mission("M1").spec
    folded = mission("M3").folded
    if m1 is None or folded is None:
        raise SpecError("The mission ladder no longer carries M1 as a part or M3 as a folded sheet.")
    m3 = folded.spec
    roller = _m6_roller_spec(_M6_BELT_WIDTH_MM)

    return (
        EditCase(
            "m1-thicker",
            m1,
            "Make the bracket 10 mm thick.",
            m1.set_parameter("thick_mm", 10.0),
        ),
        EditCase(
            "m1-wider",
            m1,
            "Make the bracket 140 mm wide and keep its depth.",
            m1.set_parameter("width_mm", 140.0),
        ),
        EditCase(
            "m1-bore",
            m1,
            "Open the bore up to 16 mm diameter.",
            m1.set_parameter("bore_mm", 16.0),
        ),
        EditCase(
            "m1-corners",
            m1,
            "Reduce the corner radius to 3 mm.",
            m1.set_parameter("corner_mm", 3.0),
        ),
        EditCase(
            "m1-sharp",
            m1,
            "Leave the four corners sharp: take the corner rounds off.",
            _without(m1, "bracket.corners"),
        ),
        EditCase(
            "m1-steel",
            m1,
            "Make the bracket from 1018 steel instead of aluminium. Change nothing else.",
            _with_material(m1, "steel-1018"),
        ),
        EditCase(
            "m6-od",
            roller,
            "Use a 60 mm outside diameter roller with the same wall.",
            roller.set_parameter("od_mm", 60.0),
        ),
        EditCase(
            "m6-wall",
            roller,
            "Make the roller tube wall 4 mm thick, keeping the outside diameter.",
            roller.set_parameter("wall_mm", 4.0),
        ),
        EditCase(
            "m6-length",
            roller,
            "Make the roller face 650 mm long.",
            roller.set_parameter("length_mm", 650.0),
        ),
        EditCase(
            "m3-thicker",
            m3,
            "Make the cover from 2 mm sheet instead of 1.5 mm.",
            _m3_spec(thickness_mm=2.0),
        ),
        EditCase(
            "m3-one-gland",
            m3,
            "Remove the second cable gland hole and keep the first where it is.",
            _m3_spec(hole_u_mm=_M3_HOLE_U_MM[:1]),
        ),
        EditCase(
            "m3-taller",
            m3,
            "Make the cover 70 mm tall.",
            _m3_spec(height_mm=70.0),
        ),
    )


__all__ = ["mission_cases"]
