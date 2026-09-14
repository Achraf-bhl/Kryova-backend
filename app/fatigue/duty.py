"""A duty cycle: several operating modes, each repeated, the sequence repeated over a life.

Master plan 8.4. `Assessment` takes one block and a repetition count, which is exactly
right for a part that does one thing forever and wrong for every machine that does not.
A press idles, strokes, over-strokes at die setting, and is switched off at night; a
swingarm sees town, motorway, a kerb and a pothole. A life is those modes in order, each
repeated, and the whole sequence repeated.

**The error this module exists to prevent is the missing transition cycle.** Count each
mode on its own, multiply by its repetitions and add, and the largest cycle the part ever
sees is not in the sum: it runs from the lowest trough of one mode to the highest peak of
another, and it closes once per pass of the sequence, not once per block. It is often the
most damaging cycle in the spectrum by a wide margin, because damage goes as the range to
the power of the slope, and it is invisible to a per-mode sum. Aircraft engineers call it
the ground-air-ground cycle; a press has one per shift.

**How the whole life is counted without expanding it.** A life of a million passes cannot
be written out and rainflow-counted. It does not need to be, because the three-point count
has a property that makes the life's count a short sum of small counts:

    count(A repeated r times)      = r · closed(A)  +  (r − 1) · closed(R ⧺ R)
    count(S repeated N times)      = N · closed(S)  +  (N − 1) · closed(Rs ⧺ Rs)

where R is the residue a block leaves unclosed, `closed(R ⧺ R)` is what closes where one
repetition meets the next, S is the sequence of the modes' residues, and Rs is its residue.
Both lines rest on one fact: **the residue of R ⧺ R is R again**, so every further
repetition closes the same cycles at its join and leaves the same residue behind.

That fact is not assumed. It is checked on every count, and a duty cycle for which it did
not hold would be refused rather than extrapolated (`DutyError`). And the whole
decomposition was checked before it was written: against brute-force counting of the
expanded history for 4,000 random duty cycles of one to four modes, including integer
plateaus and repeated values, with zero mismatches in the counted cycles and in the final
residue (2026-09-14). The test file repeats that comparison, so the claim has something to
open. **The counting is still pyLife's** — Decision 2 — this module only decides which
small histories to hand it and how many times each result occurs.

What a pass of the sequence *is* — which modes, in which order, how often each repeats —
is the judgement, and every number of it carries a source.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.fatigue.backend import FatigueBackend, PyLifeBackend
from app.fatigue.errors import FatigueError
from app.fatigue.history import Collective, CycleBlock, LoadHistory, SignConvention, StressBasis
from app.fatigue.sources import require_source

#: Two turning points agree when they differ by less than this, in MPa. The residues
#: compared are copies of the same sample values, so any real difference is orders of
#: magnitude larger; this only absorbs the pandas round trip.
_RESIDUE_TOLERANCE_MPA = 1e-9


class DutyError(FatigueError):
    """A duty cycle whose life count cannot be decomposed exactly, so is not attempted."""


@dataclass(frozen=True)
class Mode:
    """One operating mode: a block of stress history and how many times it repeats in a row.

    `repetitions` is per pass of the duty cycle, not per life, so one mode definition
    serves a duty cycle run a thousand times or a million. It may be fractional — a mode
    described as 3.5 hours of a 20-second block is 630 repetitions — and when it is, the
    cycles closed between repetitions are counted in proportion, which is exact only for
    whole blocks; the count says so.
    """

    name: str
    history: LoadHistory
    repetitions: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source", require_source(self.source, f"The repetition count of mode {self.name!r}")
        )
        if not self.name.strip():
            raise ValueError("A mode must be named; the damage is reported per mode.")
        if not (math.isfinite(self.repetitions) and self.repetitions >= 1.0):
            raise ValueError(
                f"Mode {self.name!r} must repeat at least once per pass of the duty cycle; got "
                f"{self.repetitions!r}. A mode that happens less often than once a pass belongs in "
                "a separate duty cycle with fewer passes, not in a fraction of this one."
            )

    @classmethod
    def from_hours(
        cls,
        name: str,
        history: LoadHistory,
        *,
        hours: float,
        block_seconds: float,
        source: str,
    ) -> Mode:
        """A mode stated as time: `hours` of operation made of blocks `block_seconds` long."""
        if not (math.isfinite(block_seconds) and block_seconds > 0.0):
            raise ValueError("A block lasts a positive, finite number of seconds.")
        if not (math.isfinite(hours) and hours > 0.0):
            raise ValueError("A mode runs for a positive, finite number of hours.")
        return cls(
            name=name,
            history=history,
            repetitions=hours * 3600.0 / block_seconds,
            source=f"{hours:g} h in {block_seconds:g} s blocks; {source}",
        )


@dataclass(frozen=True)
class DutyCycle:
    """Modes in the order they occur, and how many times that sequence occurs in a life."""

    name: str
    modes: tuple[Mode, ...]
    passes: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A duty cycle's pass count"))
        object.__setattr__(self, "modes", tuple(self.modes))
        if not self.modes:
            raise ValueError("A duty cycle needs at least one mode.")
        names = [m.name for m in self.modes]
        if len(set(names)) != len(names):
            raise ValueError(f"Two modes share a name ({names}); the damage is reported per mode.")
        if not (math.isfinite(self.passes) and self.passes >= 1.0):
            raise ValueError(
                f"A duty cycle occurs at least once in a life; got {self.passes!r} passes."
            )
        unsigned = [m.name for m in self.modes if m.history.sign is SignConvention.UNSIGNED]
        if unsigned:
            raise ValueError(
                f"Mode(s) {unsigned} carry an unsigned history (a von Mises norm or similar). "
                "Rainflow counting a norm reports every reversal as two half-range cycles, and "
                "joining modes makes it worse; supply signed histories (app.fatigue.field)."
            )
        bases = {m.history.basis for m in self.modes}
        if len(bases) != 1:
            raise ValueError(
                f"Every mode must be read at the same kind of location; got {sorted(bases)}. A "
                "nominal and a notch-root stress cannot be joined into one history."
            )

    @property
    def basis(self) -> StressBasis:
        return self.modes[0].history.basis


@dataclass(frozen=True)
class ModeCount:
    """The cycles one mode closes over the life, including those between its own repetitions."""

    mode: str
    within_block: Collective
    between_repetitions: Collective

    @property
    def total_cycles(self) -> float:
        return self.within_block.total_cycles + self.between_repetitions.total_cycles


@dataclass(frozen=True)
class CountedDuty:
    """Every cycle of the design life, and where each group of them came from."""

    collective: Collective
    modes: tuple[ModeCount, ...]
    #: Cycles that close only because modes follow one another — the transition cycles.
    transitions: Collective
    #: What never closes over the whole life: the turning points left at the end.
    residue_mpa: tuple[float, ...]
    assumptions: tuple[str, ...]


def count(duty: DutyCycle, backend: FatigueBackend | None = None) -> CountedDuty:
    """Count the whole life of `duty` exactly, without expanding it. See the module docstring."""
    backend = backend or PyLifeBackend()
    assumptions: list[str] = []
    passes = duty.passes
    per_mode: list[ModeCount] = []
    sequence: list[float] = []

    for mode in duty.modes:
        if mode.history.is_flat:
            # A flat block closes nothing and leaves its one value as its residue.
            per_mode.append(
                ModeCount(
                    mode=mode.name,
                    within_block=_empty(duty, f"mode {mode.name!r}: flat, closes no cycles"),
                    between_repetitions=_empty(duty, f"mode {mode.name!r}: flat"),
                )
            )
            sequence.append(mode.history.values_mpa[0])
            continue
        counted = backend.count(mode.history)
        residue = list(counted.residue_mpa)
        joins = _empty(duty, f"mode {mode.name!r}: no repetition joins")
        if mode.repetitions > 1.0 and len(residue) >= 2:
            joins = _count_join(backend, duty, residue, f"mode {mode.name!r}")
        if not float(mode.repetitions).is_integer():
            assumptions.append(
                f"Mode {mode.name!r} repeats {mode.repetitions:g} times, which is not a whole number; "
                "the cycles closed between repetitions were counted in proportion, which is exact "
                "only for whole blocks."
            )
        per_mode.append(
            ModeCount(
                mode=mode.name,
                within_block=_times(counted, mode.repetitions * passes),
                between_repetitions=_times(joins, (mode.repetitions - 1.0) * passes),
            )
        )
        sequence.extend(residue)

    transitions = _empty(duty, "no transitions between modes")
    life_residue = tuple(sequence)
    if len(duty.modes) > 1 or passes > 1.0:
        if _varies(sequence):
            joined = backend.count(_history(duty, sequence, "the sequence of the modes' residues"))
            once = _times(joined, passes)
            life_residue = joined.residue_mpa
            between_passes = _empty(duty, "no joins between passes")
            if passes > 1.0 and len(joined.residue_mpa) >= 2:
                between_passes = _count_join(backend, duty, list(joined.residue_mpa), "the duty cycle")
            transitions = _merge(
                [once, _times(between_passes, passes - 1.0)],
                duty,
                "cycles closed between modes and between passes",
            )
    if not float(passes).is_integer():
        assumptions.append(
            f"The duty cycle occurs {passes:g} times, which is not a whole number; the cycles closed "
            "between passes were counted in proportion."
        )

    pieces: list[Collective] = []
    for m in per_mode:
        pieces.extend([m.within_block, m.between_repetitions])
    pieces.append(transitions)
    life = _merge(pieces, duty, _life_source(duty))
    life = Collective(
        blocks=life.blocks,
        basis=duty.basis,
        sign=SignConvention.SIGNED,
        source=life.source,
        counting_method=(
            f"three-point rainflow via {getattr(backend, 'version', backend.name)}, over "
            f"{len(duty.modes)} mode(s) and {passes:g} pass(es), decomposed into block, "
            "repetition-join and transition counts (app.fatigue.duty)"
        ),
        residue_mpa=tuple(life_residue) if len(life_residue) >= 2 else (),
    )
    return CountedDuty(
        collective=life,
        modes=tuple(per_mode),
        transitions=transitions,
        residue_mpa=life.residue_mpa,
        assumptions=tuple(assumptions),
    )


def _count_join(
    backend: FatigueBackend, duty: DutyCycle, residue: list[float], what: str
) -> Collective:
    """What closes where one repetition of `residue` meets the next — and the check that licenses it."""
    doubled = residue + residue
    joined = backend.count(_history(duty, doubled, f"{what}: two consecutive residues"))
    again = list(joined.residue_mpa)
    if len(again) != len(residue) or any(
        abs(a - b) > _RESIDUE_TOLERANCE_MPA for a, b in zip(again, residue, strict=True)
    ):
        raise DutyError(
            f"The residue of two joined repetitions of {what} is not that block's own residue, so "
            "the count of one join cannot be multiplied by the number of repetitions. Expand the "
            "repetitions into one longer history and count it directly."
        )
    return joined


def _history(duty: DutyCycle, values: Sequence[float], what: str) -> LoadHistory:
    points = list(values)
    if len(points) < 3:
        # A residue of two turning points joined to itself: pad by repeating the last
        # point, which adds no turning point and so changes no count.
        points = points + [points[-1]] * (3 - len(points))
    return LoadHistory.from_values(
        f"{duty.name}: {what}",
        points,
        basis=duty.basis,
        sign=SignConvention.SIGNED,
        source=f"{what}, derived from duty cycle {duty.name!r} [{duty.source}]",
    )


def _varies(values: Sequence[float]) -> bool:
    return len(values) >= 2 and max(values) - min(values) > 0.0


def _times(collective: Collective, factor: float) -> Collective:
    return Collective(
        blocks=tuple(
            CycleBlock(amplitude_mpa=b.amplitude_mpa, mean_mpa=b.mean_mpa, cycles=b.cycles * factor)
            for b in collective.blocks
        ),
        basis=collective.basis,
        sign=collective.sign,
        source=collective.source,
        counting_method=collective.counting_method,
    )


def _empty(duty: DutyCycle, why: str) -> Collective:
    return Collective(blocks=(), basis=duty.basis, sign=SignConvention.SIGNED, source=why)


def _merge(pieces: Iterable[Collective], duty: DutyCycle, source: str) -> Collective:
    """One collective, with blocks of identical amplitude and mean added together."""
    totals: dict[tuple[float, float], float] = {}
    for piece in pieces:
        for block in piece.blocks:
            if block.cycles == 0.0:
                continue
            key = (block.amplitude_mpa, block.mean_mpa)
            totals[key] = totals.get(key, 0.0) + block.cycles
    blocks = tuple(
        CycleBlock(amplitude_mpa=a, mean_mpa=m, cycles=n)
        for (a, m), n in sorted(totals.items(), key=lambda kv: (-kv[0][0], kv[0][1]))
    )
    return Collective(blocks=blocks, basis=duty.basis, sign=SignConvention.SIGNED, source=source)


def _life_source(duty: DutyCycle) -> str:
    modes = "; ".join(
        f"{m.name} × {m.repetitions:g} [{m.source}] of [{m.history.source}]" for m in duty.modes
    )
    return f"duty cycle {duty.name!r}, {duty.passes:g} passes [{duty.source}]: {modes}"


__all__ = [
    "CountedDuty",
    "DutyCycle",
    "DutyError",
    "Mode",
    "ModeCount",
    "count",
]
