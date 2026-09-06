"""The search for a target runs in arithmetic, not across the model's tool calls.

Measured on the seat, 2026-09-06, ladder prompt S1 -- "make a steel
counterweight 200 mm long and get it to 2.4 kg by adjusting only its width and
height, keeping them equal". By run 4 the dimensions were drivable and
findable, and the agent did run the loop:

    set parameter -> 2.391014 kg
    set parameter -> 2.418087 kg
    set parameter -> 2.464193 kg
    set parameter -> 2.537374 kg
    set parameter -> 2.464195 kg

The 1% band around 2.4 kg is 2.376 to 2.424. It landed inside **twice**, did
not notice, wandered out again, and ran out of rounds. That is not a bug in any
one tool: a 9B model cannot hold a numerical search across tool calls, and each
guess costs a round of a budget of twenty.

A secant search is arithmetic, so it belongs in arithmetic. `catia_drive_to`
does the whole search on the workstation in one tool call, stops the moment it
is inside the tolerance, and reports the value it **measured** rather than the
one it aimed at.

Offline: a fake part whose mass is a known function of the parameter, so the
convergence itself is what is under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402

STEEL_KG_PER_MM3 = 7860e-9
LENGTH_MM = 200.0


class _Part:
    def Update(self) -> None:  # noqa: N802 - COM spelling
        return None


class _Block(CatiaCom):
    """A 200 mm bar whose mass follows its square section exactly.

    Subclassing rather than mocking, so the code under test is reached the way
    the daemon reaches it -- only the four calls that touch COM are replaced.
    """

    def __init__(self, value: float = 40.0, *, measurement: str = "mass_kg") -> None:
        self.value = value
        self.rebuilds = 0
        self.measurements = 0
        self._key = measurement

    def _part(self):  # type: ignore[override]
        return _Part()

    def _parameter_value(self, name: str) -> float:  # type: ignore[override]
        return self.value

    def _set_parameter_value(self, name: str, value: float) -> None:  # type: ignore[override]
        self.value = value
        self.rebuilds += 1

    def measure(self):  # type: ignore[override]
        self.measurements += 1
        volume = self.value * self.value * LENGTH_MM
        return {"mass_kg": volume * STEEL_KG_PER_MM3, "volume_mm3": volume}


class TestItConverges:
    def test_it_reaches_the_s1_target(self) -> None:
        """The prompt's own answer: 2.4 kg of steel, 200 mm long, is a
        39.07 mm square section."""
        block = _Block(40.0)
        result = block.drive_to(name="width", measurement="mass_kg", target=2.4)
        assert result["reached"] is True
        assert result["mass_kg"] == pytest.approx(2.4, rel=0.01)
        assert result["value"] == pytest.approx(39.07, rel=0.01)

    def test_it_takes_only_a_few_rebuilds(self) -> None:
        """The whole reason it is one tool call: a secant search on a
        monotonic measurement converges in two or three."""
        block = _Block(40.0)
        result = block.drive_to(name="width", measurement="mass_kg", target=2.4)
        assert result["attempts"] <= 4

    def test_a_long_way_off_still_converges(self) -> None:
        block = _Block(200.0)
        result = block.drive_to(name="width", measurement="mass_kg", target=2.4)
        assert result["reached"] is True
        assert result["mass_kg"] == pytest.approx(2.4, rel=0.01)

    def test_already_on_target_does_nothing(self) -> None:
        """No rebuild, because there is nothing to change."""
        block = _Block(39.07)
        result = block.drive_to(name="width", measurement="mass_kg", target=2.4)
        assert result["reached"] is True
        assert result["attempts"] == 0
        assert block.rebuilds == 0

    def test_a_tighter_tolerance_is_honoured(self) -> None:
        block = _Block(40.0)
        result = block.drive_to(
            name="width", measurement="mass_kg", target=2.4, tolerance=0.0001
        )
        assert result["mass_kg"] == pytest.approx(2.4, rel=0.0001)

    def test_volume_works_the_same(self) -> None:
        block = _Block(40.0)
        result = block.drive_to(name="width", measurement="volume_mm3", target=300_000)
        assert result["reached"] is True
        assert result["volume_mm3"] == pytest.approx(300_000, rel=0.01)


class TestItReportsWhatItMeasured:
    def test_the_value_is_the_measurement_not_the_target(self) -> None:
        block = _Block(40.0)
        result = block.drive_to(name="width", measurement="mass_kg", target=2.4)
        # The last real measurement, not 2.4 echoed back.
        assert result["mass_kg"] != 2.4
        assert result["target"] == 2.4

    def test_the_history_is_every_pair_it_measured(self) -> None:
        """So a reader can see the search rather than take the answer on
        trust."""
        block = _Block(40.0)
        result = block.drive_to(name="width", measurement="mass_kg", target=2.4)
        assert len(result["history"]) == result["attempts"] + 1
        assert all({"value", "mass_kg"} <= set(step) for step in result["history"])

    def test_a_reached_run_says_measured_not_calculated(self) -> None:
        block = _Block(40.0)
        result = block.drive_to(name="width", measurement="mass_kg", target=2.4)
        assert "Measured, not calculated" in result["note"]


class TestItRefusesToPretend:
    def test_a_run_that_does_not_converge_says_so(self) -> None:
        """An unconverged number reported as a result is the failure this
        product exists to prevent."""
        block = _Block(40.0)
        result = block.drive_to(
            name="width", measurement="mass_kg", target=2.4, max_attempts=2, tolerance=1e-9
        )
        assert result["reached"] is False
        assert "NOT reached" in result["note"]
        assert "did not converge" in result["note"]

    def test_it_leaves_the_part_at_the_best_it_measured(self) -> None:
        """Not at whatever the last extrapolation happened to be -- that
        value was never built or measured."""
        block = _Block(40.0)
        result = block.drive_to(
            name="width", measurement="mass_kg", target=2.4, max_attempts=2, tolerance=1e-9
        )
        assert block.value == pytest.approx(result["value"])
        assert result["value"] == pytest.approx(39.07, rel=0.05)

    def test_a_parameter_that_does_nothing_stops_rather_than_diverging(self) -> None:
        """Dividing by a zero slope produces an absurd value, and a loop handed
        one spends every attempt building something impossible."""

        class _Deaf(_Block):
            def measure(self):
                self.measurements += 1
                return {"mass_kg": 5.0, "volume_mm3": 1.0}

        result = _Deaf(40.0).drive_to(name="width", measurement="mass_kg", target=2.4)
        assert result["reached"] is False
        assert result["attempts"] <= 2

    def test_an_unmeasurable_property_is_refused_by_name(self) -> None:
        with pytest.raises(CatiaOperationError, match="not something this can drive"):
            _Block().drive_to(name="width", measurement="stiffness", target=1.0)

    def test_a_part_with_no_mass_says_to_set_a_material(self) -> None:
        """CATIA reports no mass until a material is assigned, and driving to a
        mass target without one would chase None."""

        class _NoMaterial(_Block):
            def measure(self):
                return {"mass_kg": None, "volume_mm3": 1.0}

        with pytest.raises(CatiaOperationError, match="material"):
            _NoMaterial().drive_to(name="width", measurement="mass_kg", target=2.4)


class TestHoldingParametersTogether:
    def test_also_moves_with_the_driver(self) -> None:
        """"keeping them equal" is this argument."""

        class _Pair(_Block):
            def __init__(self) -> None:
                super().__init__(40.0)
                self.set: list[tuple[str, float]] = []

            def _set_parameter_value(self, name: str, value: float) -> None:
                self.set.append((name, value))
                super()._set_parameter_value(name, value)

        block = _Pair()
        result = block.drive_to(
            name="width", measurement="mass_kg", target=2.4, also=["height"]
        )
        assert result["parameters"] == ["width", "height"]
        driven = {name for name, _ in block.set}
        assert driven == {"width", "height"}
        # Both land on the same value on every rebuild, which is what "equal"
        # has to mean.
        by_step = [block.set[i : i + 2] for i in range(0, len(block.set), 2)]
        assert all(step[0][1] == step[1][1] for step in by_step if len(step) == 2)
