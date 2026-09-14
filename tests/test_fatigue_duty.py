"""A duty cycle counted over a whole life without expanding it — master plan 8.4.

The oracle is the definition. For any duty cycle small enough to write out, the life's
history is expanded — every mode repeated, the sequence repeated — and counted directly
by the same pyLife counter. The decomposed count must agree with it cycle for cycle and
in the residue it leaves. A life too long to expand is checked against the exact linear
growth of the expanded count in the number of passes.
"""

from __future__ import annotations

import math
import time
from collections import Counter

import numpy as np
import pytest

from app.design.assertions import Outcome
from app.fatigue import (
    Assessment,
    Collective,
    LoadHistory,
    MeanStressPolicy,
    SignConvention,
    SNCurve,
    StressBasis,
    available,
)
from app.fatigue.backend import PyLifeBackend
from app.fatigue.duty import CountedDuty, DutyCycle, DutyError, Mode, count

pytestmark = pytest.mark.skipif(
    not available(), reason="pyLife is not installed; counting is federated to it"
)

SOURCE = "test fixture duty cycle, not a usage survey"


def _history(values, name: str = "block", basis: StressBasis = StressBasis.NOTCH_ROOT) -> LoadHistory:
    return LoadHistory.from_values(name, values, basis=basis, sign=SignConvention.SIGNED, source=SOURCE)


def _mode(name: str, values, repetitions: float = 1.0) -> Mode:
    return Mode(name=name, history=_history(values, name), repetitions=repetitions, source=SOURCE)


def _duty(*modes: Mode, passes: float = 1.0) -> DutyCycle:
    return DutyCycle(name="duty", modes=modes, passes=passes, source=SOURCE)


def _cycles(collective: Collective) -> Counter:
    """Blocks keyed on rounded (amplitude, mean) so float round-trips compare exactly."""
    totals: Counter = Counter()
    for b in collective.blocks:
        if b.cycles:
            totals[(round(b.amplitude_mpa, 6), round(b.mean_mpa, 6))] += b.cycles
    return totals


def _expanded(duty: DutyCycle) -> Collective:
    period: list[float] = []
    for mode in duty.modes:
        period.extend(list(mode.history.values_mpa) * int(mode.repetitions))
    life = period * int(duty.passes)
    return PyLifeBackend().count(_history(life, "expanded life"))


class TestTheLifeCountEqualsTheExpandedCount:
    @pytest.mark.parametrize("seed", range(40))
    def test_random_duty_cycles_agree_cycle_for_cycle(self, seed: int) -> None:
        rng = np.random.default_rng(seed)
        modes = []
        for i in range(int(rng.integers(1, 5))):
            values = np.round(rng.normal(size=int(rng.integers(3, 9))) * 50.0 + rng.normal() * 80.0, 3)
            modes.append(_mode(f"m{i}", values.tolist(), float(rng.integers(1, 5))))
        duty = _duty(*modes, passes=float(rng.integers(1, 5)))
        counted = count(duty)
        brute = _expanded(duty)
        assert _cycles(counted.collective) == _cycles(brute)
        assert counted.residue_mpa == pytest.approx(brute.residue_mpa)

    @pytest.mark.parametrize("seed", range(20))
    def test_integer_plateaus_and_repeated_values_agree_too(self, seed: int) -> None:
        rng = np.random.default_rng(1000 + seed)
        modes = [
            _mode(f"m{i}", rng.integers(-9, 10, size=int(rng.integers(3, 7))).astype(float).tolist(), float(rng.integers(1, 4)))
            for i in range(int(rng.integers(1, 4)))
        ]
        duty = _duty(*modes, passes=float(rng.integers(1, 4)))
        assert _cycles(count(duty).collective) == _cycles(_expanded(duty))


class TestTheTransitionCycle:
    """±100 about 0, then ±50 about 300: the life's largest cycle belongs to neither mode."""

    A = [0.0, 100.0, -100.0, 100.0, -100.0, 0.0]
    B = [300.0, 350.0, 250.0, 350.0, 250.0, 300.0]

    def test_a_per_mode_sum_never_sees_it_and_the_duty_count_does(self) -> None:
        duty = _duty(_mode("low", self.A, 10.0), _mode("high", self.B, 10.0), passes=5.0)
        counted = count(duty)
        largest = max(b.range_mpa for b in counted.collective.blocks if b.cycles)
        assert largest == pytest.approx(450.0)  # from −100 to 350
        per_mode_only = max(
            b.range_mpa
            for m in counted.modes
            for part in (m.within_block, m.between_repetitions)
            for b in part.blocks
        )
        assert per_mode_only == pytest.approx(200.0)

    def test_it_is_counted_among_the_transitions(self) -> None:
        duty = _duty(_mode("low", self.A, 10.0), _mode("high", self.B, 10.0), passes=5.0)
        assert any(b.range_mpa == pytest.approx(450.0) for b in count(duty).transitions.blocks)

    def test_an_idle_mode_is_a_level_the_transition_reaches(self) -> None:
        idle = _mode("idle", [200.0, 200.0, 200.0], 1.0)
        duty = _duty(_mode("work", self.A, 3.0), idle, passes=4.0)
        counted = count(duty)
        assert _cycles(counted.collective) == _cycles(_expanded(duty))
        assert max(b.range_mpa for b in counted.collective.blocks) == pytest.approx(300.0)


class TestALongLifeIsNotExpanded:
    def test_a_million_passes_grow_exactly_linearly_from_the_expanded_counts(self) -> None:
        low = _mode("low", TestTheTransitionCycle.A, 7.0)
        high = _mode("high", TestTheTransitionCycle.B, 3.0)
        small = {n: _cycles(_expanded(_duty(low, high, passes=float(n)))) for n in (2, 3)}
        per_pass = Counter({k: small[3][k] - small[2].get(k, 0) for k in small[3]})
        offset = Counter({k: small[2][k] - 2 * per_pass.get(k, 0) for k in small[2]})
        n = 1_000_000
        predicted = Counter({k: offset.get(k, 0) + n * per_pass.get(k, 0) for k in set(per_pass) | set(offset)})
        predicted = Counter({k: v for k, v in predicted.items() if v})
        started = time.perf_counter()
        counted = count(_duty(low, high, passes=float(n)))
        elapsed = time.perf_counter() - started
        assert _cycles(counted.collective) == predicted
        assert elapsed < 5.0

    def test_the_damage_of_a_life_is_the_hand_sum_over_its_blocks(self) -> None:
        duty = _duty(
            _mode("low", TestTheTransitionCycle.A, 10.0),
            _mode("high", TestTheTransitionCycle.B, 10.0),
            passes=1000.0,
        )
        counted = count(duty)
        curve = SNCurve(slope_k1=5.0, slope_k2=5.0, knee_cycles=1e6, knee_amplitude_mpa=100.0, source=SOURCE)
        result = Assessment(
            loading=counted.collective,
            curve=curve,
            required_factors=frozenset(),
            mean_stress_policy=MeanStressPolicy.DECLARED_IRRELEVANT,
            mean_stress_justification="test: the arithmetic under test is the count, not the Haigh diagram",
        ).run()
        assert result.outcome is not Outcome.UNMEASURED
        by_hand = sum(b.cycles / (1e6 * (b.amplitude_mpa / 100.0) ** -5.0) for b in counted.collective.blocks)
        assert result.damage == pytest.approx(by_hand, rel=1e-9)


class TestWhatACountSaysAboutItself:
    def test_a_fractional_repetition_is_stated(self) -> None:
        counted = count(_duty(_mode("m", TestTheTransitionCycle.A, 2.5)))
        assert any("not a whole number" in a for a in counted.assumptions)

    def test_the_source_names_every_mode_and_its_repetition_source(self) -> None:
        counted = count(_duty(_mode("stroke", TestTheTransitionCycle.A, 2.0)))
        assert "stroke × 2" in counted.collective.source
        assert "app.fatigue.duty" in counted.collective.counting_method

    def test_hours_become_repetitions(self) -> None:
        mode = Mode.from_hours("town", _history(TestTheTransitionCycle.A), hours=3.5, block_seconds=20.0, source=SOURCE)
        assert mode.repetitions == pytest.approx(630.0)
        assert "3.5 h" in mode.source

    def test_a_life_whose_joins_do_not_repeat_is_refused_not_extrapolated(self) -> None:
        class Liar(PyLifeBackend):
            def count(self, history: LoadHistory) -> Collective:
                counted = super().count(history)
                if "two consecutive residues" in history.name:
                    return Collective(
                        blocks=counted.blocks,
                        basis=counted.basis,
                        sign=counted.sign,
                        source=counted.source,
                        residue_mpa=counted.residue_mpa + (1.0,),
                    )
                return counted

        with pytest.raises(DutyError, match="Expand the repetitions"):
            count(_duty(_mode("m", TestTheTransitionCycle.A, 3.0)), backend=Liar())


class TestRefusals:
    def test_an_unsigned_mode_is_refused(self) -> None:
        vm = LoadHistory.from_von_mises("vm", [0.0, 1.0, 0.0], source=SOURCE)
        with pytest.raises(ValueError, match="unsigned"):
            _duty(Mode(name="vm", history=vm, repetitions=1.0, source=SOURCE))

    def test_modes_read_at_different_kinds_of_location_are_refused(self) -> None:
        nominal = Mode("n", _history([0.0, 1.0, 0.0], basis=StressBasis.NOMINAL), 1.0, SOURCE)
        with pytest.raises(ValueError, match="same kind of location"):
            _duty(_mode("local", [0.0, 1.0, 0.0]), nominal)

    def test_a_mode_must_repeat_at_least_once(self) -> None:
        for bad in (0.5, 0.0, math.inf, math.nan):
            with pytest.raises(ValueError, match="at least once"):
                _mode("m", [0.0, 1.0, 0.0], bad)

    def test_a_duty_cycle_occurs_at_least_once(self) -> None:
        with pytest.raises(ValueError, match="at least once in a life"):
            _duty(_mode("m", [0.0, 1.0, 0.0]), passes=0.5)

    def test_counts_need_sources(self) -> None:
        with pytest.raises(ValueError, match="source"):
            Mode("m", _history([0.0, 1.0, 0.0]), 1.0, source="")
        with pytest.raises(ValueError, match="source"):
            DutyCycle(name="d", modes=(_mode("m", [0.0, 1.0, 0.0]),), passes=1.0, source=" ")

    def test_an_empty_duty_cycle_and_duplicate_modes_are_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one mode"):
            DutyCycle(name="d", modes=(), passes=1.0, source=SOURCE)
        with pytest.raises(ValueError, match="share a name"):
            _duty(_mode("m", [0.0, 1.0, 0.0]), _mode("m", [0.0, 2.0, 0.0]))

    def test_hours_and_block_length_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            Mode.from_hours("m", _history([0.0, 1.0, 0.0]), hours=1.0, block_seconds=0.0, source=SOURCE)
        with pytest.raises(ValueError, match="positive"):
            Mode.from_hours("m", _history([0.0, 1.0, 0.0]), hours=-1.0, block_seconds=1.0, source=SOURCE)


def test_counted_duty_is_exported() -> None:
    assert CountedDuty.__name__ == "CountedDuty"
