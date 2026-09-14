"""What an optimisation is run against — `app/optimise/models.py`, master plan 10.3.

Three models behind one protocol. The claims worth pinning are the ones that
would be quiet if they broke: a spec model that edits the caller's spec in place,
a derived parameter accepted and then overwritten on resolve, and a part model
that reuses one document so a half-applied set survives into the next point.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.design.params import Parameter, ParameterSet, Unit
from app.design.spec import DesignSpec
from app.optimise import (
    AnalyticModel,
    DesignVariable,
    Evaluator,
    Objective,
    OptimisationError,
    OptimisationProblem,
    PartModel,
    SpecModel,
    optimise,
)


def _plate() -> DesignSpec:
    return DesignSpec(
        name="plate",
        parameters=ParameterSet.of(
            [
                Parameter(name="width_mm", value=40.0, unit=Unit.MM),
                Parameter(name="thickness_mm", value=4.0, unit=Unit.MM),
                Parameter(name="half_width_mm", expression="width_mm / 2", unit=Unit.MM),
            ]
        ),
    )


def _value(spec: DesignSpec, name: str) -> float | None:
    for one in spec.parameters:
        if one.name == name:
            return one.value
    raise KeyError(name)


class TestAnAnalyticModelIsACallable:
    def test_the_baseline_is_a_copy(self) -> None:
        start = {"x": 1.0}
        model = AnalyticModel(response=lambda v: {"y": v["x"]}, start=start)
        baseline = model.baseline()
        baseline["x"] = 99.0  # type: ignore[index]
        assert model.baseline() == {"x": 1.0}
        assert model.evaluate({"x": 3.0}) == {"y": 3.0}
        assert model.backend == "analytic"


class TestASpecModelMovesOnlyFreeParameters:
    def test_the_baseline_leaves_out_what_is_derived(self) -> None:
        model = SpecModel(spec=_plate(), probe=lambda spec: {})
        assert model.baseline() == {"width_mm": 40.0, "thickness_mm": 4.0}

    def test_at_returns_a_new_spec_and_leaves_the_original_alone(self) -> None:
        original = _plate()
        moved = SpecModel(spec=original, probe=lambda spec: {}).at({"width_mm": 60.0})
        assert _value(moved, "width_mm") == 60.0
        assert _value(moved, "thickness_mm") == 4.0
        assert _value(original, "width_mm") == 40.0

    def test_an_unknown_parameter_is_refused_naming_what_is_declared(self) -> None:
        model = SpecModel(spec=_plate(), probe=lambda spec: {})
        with pytest.raises(OptimisationError, match="no parameter called 'depth_mm'") as refused:
            model.at({"depth_mm": 1.0})
        assert "width_mm" in str(refused.value)

    def test_a_derived_parameter_is_refused(self) -> None:
        model = SpecModel(spec=_plate(), probe=lambda spec: {})
        with pytest.raises(OptimisationError, match="derived parameter"):
            model.at({"half_width_mm": 10.0})

    def test_evaluate_hands_the_moved_spec_to_the_probe(self) -> None:
        seen: list[DesignSpec] = []

        def probe(spec: DesignSpec) -> Mapping[str, Any]:
            seen.append(spec)
            width, thickness = _value(spec, "width_mm"), _value(spec, "thickness_mm")
            assert width is not None and thickness is not None
            return {"area_mm2": width * thickness}

        payload = SpecModel(spec=_plate(), probe=probe).evaluate({"thickness_mm": 2.0})
        assert payload == {"area_mm2": 80.0}
        assert _value(seen[0], "thickness_mm") == 2.0

    def test_a_spec_model_optimises_to_the_closed_form(self) -> None:
        """Minimise (w − 25)² over w in [10, 50]: the answer is w = 25 exactly."""

        def probe(spec: DesignSpec) -> Mapping[str, Any]:
            width = _value(spec, "width_mm")
            assert width is not None
            return {"miss": (width - 25.0) ** 2}

        problem = OptimisationProblem(
            name="centre",
            variables=(DesignVariable(name="width_mm", lower=10.0, upper=50.0),),
            objective=Objective(measurement="miss"),
        )
        result = optimise(problem, SpecModel(spec=_plate(), probe=probe))
        assert result.converged
        assert result.values is not None
        assert result.values["width_mm"] == pytest.approx(25.0, abs=1e-3)


class _Runner:
    """A part runner double: holds its own parameters, refuses what will not build."""

    def __init__(self, journal: list[str], *, refuse: str | None = None) -> None:
        self.parameters = {"Pad.1\\length_mm": 20.0, "Pocket.1\\depth_mm": 5.0}
        self.journal = journal
        self.refuse = refuse

    def __call__(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        self.journal.append(tool)
        if tool == "catia_list_parameters":
            return {
                "parameters": [
                    {"name": name, "value": value} for name, value in self.parameters.items()
                ]
                + [{"name": "Sketch.1\\label", "value": "not a number"}]
            }
        if tool == "catia_set_parameter":
            if arguments["name"] == self.refuse:
                raise ValueError(f"{arguments['name']} cannot be {arguments['value']}.")
            self.parameters[arguments["name"]] = arguments["value"]
            return {"ok": True}
        if tool == "catia_measure":
            return {"volume_mm3": self.parameters["Pad.1\\length_mm"] * 100.0}
        raise AssertionError(tool)


class TestAPartModelBuildsAFreshPartEveryTime:
    def test_the_baseline_is_every_numeric_journal_dimension(self) -> None:
        model = PartModel(build=lambda: _Runner([]))
        assert model.baseline() == {"Pad.1\\length_mm": 20.0, "Pocket.1\\depth_mm": 5.0}

    def test_a_part_that_cannot_be_built_has_an_empty_baseline(self) -> None:
        def broken() -> Any:
            raise RuntimeError("the kernel is gone")

        assert PartModel(build=broken).baseline() == {}

    def test_each_evaluation_builds_its_own_runner(self) -> None:
        built: list[_Runner] = []

        def build() -> _Runner:
            runner = _Runner([])
            built.append(runner)
            return runner

        model = PartModel(build=build)
        assert model.evaluate({"Pad.1\\length_mm": 30.0}) == {"volume_mm3": 3000.0}
        assert model.evaluate({"Pad.1\\length_mm": 40.0}) == {"volume_mm3": 4000.0}
        assert len(built) == 2
        assert built[0].parameters["Pad.1\\length_mm"] == 30.0
        assert built[0].journal == ["catia_set_parameter", "catia_measure"]

    def test_a_refused_set_leaves_nothing_behind_for_the_next_point(self) -> None:
        """The fourth-variable case the module docstring names: a refusal half way
        through a set must not leave the earlier sets applied to the next build."""
        built: list[_Runner] = []

        def build() -> _Runner:
            runner = _Runner([], refuse="Pocket.1\\depth_mm" if not built else None)
            built.append(runner)
            return runner

        problem = OptimisationProblem(
            name="pad",
            variables=(
                DesignVariable(name="Pad.1\\length_mm", lower=10.0, upper=50.0),
                DesignVariable(name="Pocket.1\\depth_mm", lower=1.0, upper=9.0),
            ),
            objective=Objective(measurement="volume_mm3"),
        )
        evaluator = Evaluator(problem, PartModel(build=build))
        failed = evaluator.at({"Pad.1\\length_mm": 45.0, "Pocket.1\\depth_mm": 8.0})
        assert not failed.built
        assert "cannot be 8.0" in failed.reason

        after = evaluator.at({"Pocket.1\\depth_mm": 2.0})
        assert after.built
        assert built[1].parameters["Pad.1\\length_mm"] == 20.0
        assert after.objective == 2000.0
