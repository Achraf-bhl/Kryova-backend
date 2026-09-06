"""The seam between our vocabulary and somebody else's fatigue arithmetic.

**Decision 2: physics is federated, never re-implemented.** Rainflow counting and
Palmgren–Miner summation are textbook, and a from-scratch three-point counter is about
sixty lines — which is exactly the trap. A hand-rolled counter is a hundred lines of
residue handling, hysteresis-loop bookkeeping and edge cases that nobody outside this
repository has ever exercised, presented with the same confidence as a library four
hundred engineers have run against test data. The counter here is
`pylife.stress.rainflow` (Bosch Research, Apache-2.0), the damage summation is
`pylife.strength.fatigue`, and the mean-stress transformation is
`pylife.strength.meanstress`. This module converts between their pandas signals and our
dataclasses, and does no fatigue arithmetic of its own.

The ABC is what the decision buys. FFPACK and fatpack are named beside pyLife in the
technology register, both bring counters with different residue conventions, and a
future assessment against an FKM-nonlinear or a critical-plane method will want a
different engine entirely. They drop in behind `FatigueBackend` without
`app/fatigue/assessment.py` — which is where the judgements live — knowing which ran.
What the backend *is* still reaches the result: `Method.library` records the name and
version, so a damage number stays bound to the code that produced it (Decision 3).

**The package imports without pyLife installed**, the same contract `app/kernel/`
keeps for OCCT and `app/catia/` for pywin32: `available()` probes, `require()` refuses
with an actionable message, and `Assessment.run` turns the refusal into an
`UNMEASURED` verdict rather than an exception. That is what keeps the rest of the
suite runnable on a machine that has not installed pandas and h5py.

One conversion is worth naming because it is the kind that goes wrong silently:
pyLife's load collectives are keyed on **range and mean**, ours on **amplitude and
mean**, and the factor of two lives in `_to_frame` / `_from_frame` and nowhere else.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any

from app.fatigue.errors import BackendUnavailable
from app.fatigue.history import (
    Collective,
    CycleBlock,
    LoadHistory,
    SignConvention,
)
from app.fatigue.material import SNCurve

_IMPORT_ERROR: str = ""

try:  # pragma: no cover - exercised by whether the dependency is installed
    import numpy as _np
    import pandas as _pd
    import pylife as _pylife
    import pylife.strength.fatigue as _pylife_fatigue  # noqa: F401  (registers accessors)
    import pylife.stress.collective as _pylife_collective  # noqa: F401  (registers accessors)
    from pylife.strength.meanstress import HaighDiagram as _HaighDiagram
    from pylife.stress.rainflow import FullRecorder as _FullRecorder
    from pylife.stress.rainflow import ThreePointDetector as _ThreePointDetector
except Exception as exc:  # pragma: no cover - only on an install without pyLife
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def available() -> bool:
    """Whether a fatigue backend can actually run here."""
    return not _IMPORT_ERROR


def import_error() -> str:
    """Why the backend is unavailable, or an empty string when it is available."""
    return _IMPORT_ERROR


def require() -> None:
    """Refuse, with the command that fixes it, rather than failing deep inside pandas."""
    if _IMPORT_ERROR:
        raise BackendUnavailable(
            "Fatigue assessment needs pyLife, which is not importable here "
            f"({_IMPORT_ERROR}). Install it with `pip install pylife` — it is in "
            "requirements.txt and pulls pandas and h5py with it."
        )


def library_version() -> str:
    """The backend library and version, for the result's `Method`."""
    if _IMPORT_ERROR:
        return ""
    return f"pyLife {getattr(_pylife, '__version__', 'unknown')}"


class FatigueBackend(ABC):
    """Mesh-free fatigue arithmetic: count a history, sum the damage, correct a mean.

    Narrow on purpose, exactly like `solve.Solver`. Everything that is a
    *judgement* — which factors are required, whether a notch may be applied,
    what happens below the knee — lives above this in `assessment.py`, so a
    second engine can be dropped in without any of it moving.
    """

    name: str
    version: str

    @abstractmethod
    def count(self, history: LoadHistory) -> Collective:
        """Rainflow-count a stress history into closed cycles plus a residue."""

    @abstractmethod
    def cycles_to_failure(self, curve: SNCurve, amplitude_mpa: float) -> float:
        """N at one amplitude off the curve. `inf` below an endurance limit with no k2."""

    @abstractmethod
    def damage(self, collective: Collective, curve: SNCurve) -> tuple[float, ...]:
        """Palmgren–Miner damage per block. The caller sums; per-block is what a
        reviewer needs to see which part of the duty cycle is eating the life."""

    @abstractmethod
    def correct_mean_stress(self, collective: Collective, *, m: float, m2: float | None) -> Collective:
        """Transform every block to the equivalent fully-reversed amplitude (R = −1)."""

    @abstractmethod
    def at_failure_probability(self, curve: SNCurve, probability: float) -> SNCurve:
        """The same curve restated at another survival probability, using its scatter."""


class PyLifeBackend(FatigueBackend):
    """pyLife 2.x behind `FatigueBackend`.

    Counting is pyLife's **three-point** detector (`ThreePointDetector`), which is
    the ASTM E1049 rainflow algorithm. The four-point variant is also available in
    the library and gives the same closed cycles for the histories a machine duty
    cycle produces; three-point is chosen because its residue semantics are the
    ones documented in the standard and the residue is reported rather than
    absorbed. `Collective.residue_mpa` carries the unclosed turning points.
    """

    name = "pylife"

    def __init__(self) -> None:
        require()
        self.version = library_version()

    # -- counting ---------------------------------------------------------

    def count(self, history: LoadHistory) -> Collective:
        """Count one block of a history.

        Refuses an unsigned history. `app/fatigue/history.py` explains why at
        length; the short version is that rainflow counting a norm reports twice
        the cycles at half the range, and no post-hoc correction recovers the
        sign that was thrown away upstream.
        """
        require()
        if history.sign is SignConvention.UNSIGNED:
            raise ValueError(
                "This history is unsigned (a von Mises norm or similar), and rainflow "
                "counting it would report every fully reversed cycle as two half-range "
                "cycles. Supply a signed scalar stress — signed von Mises, a principal "
                "stress, or a component along a stated direction."
            )
        detector = _ThreePointDetector(recorder=_FullRecorder())
        detector.process(_np.asarray(history.values_mpa, dtype=float))
        recorder = detector.recorder
        frame = recorder.collective
        blocks: tuple[CycleBlock, ...] = ()
        if len(frame) > 0:
            load = frame.load_collective
            amplitudes = _np.asarray(load.amplitude, dtype=float)
            means = _np.asarray(load.meanstress, dtype=float)
            counts = _np.asarray(load.cycles, dtype=float)
            blocks = tuple(
                CycleBlock(amplitude_mpa=float(a), mean_mpa=float(m), cycles=float(n))
                for a, m, n in zip(amplitudes, means, counts, strict=True)
            )
        residue = tuple(float(v) for v in _np.asarray(detector.residuals, dtype=float))
        return Collective(
            blocks=blocks,
            basis=history.basis,
            sign=history.sign,
            source=history.source,
            counting_method=(
                f"three-point rainflow (ASTM E1049) via {self.version}, one block of "
                f"{len(history.values_mpa)} samples"
            ),
            residue_mpa=residue,
        )

    # -- the curve --------------------------------------------------------

    def cycles_to_failure(self, curve: SNCurve, amplitude_mpa: float) -> float:
        require()
        value = _to_woehler(curve).fatigue.cycles(float(amplitude_mpa))
        return float(value)

    def at_failure_probability(self, curve: SNCurve, probability: float) -> SNCurve:
        """Restate the curve at another survival probability, from its declared scatter.

        Refuses a curve with no scatter. There is no defensible transformation
        from a median curve to a 97.5% survival curve without knowing how wide
        the test scatter was, and inventing one would put a reliability figure on
        a design that has none — precisely the unmeasured claim Decision 3
        forbids. The assessment turns this refusal into `UNMEASURED` naming the
        missing scatter.
        """
        require()
        if not curve.has_scatter:
            raise ValueError(
                "This S-N curve declares no scatter (TN or TS), so it cannot be restated "
                f"at a failure probability of {probability:g}. Supply the scatter from the "
                "test series the curve came from, or assess against the curve as given and "
                "report the probability it was measured at."
            )
        transformed = _to_woehler(curve).woehler.transform_to_failure_probability(probability)
        series = transformed.to_pandas()
        return SNCurve(
            slope_k1=float(series["k_1"]),
            slope_k2=(None if not math.isfinite(float(series["k_2"])) else float(series["k_2"])),
            knee_cycles=float(series["ND"]),
            knee_amplitude_mpa=float(series["SD"]),
            scatter_tn=float(series["TN"]),
            scatter_ts=float(series["TS"]),
            failure_probability=float(probability),
            mean_stress_sensitivity=curve.mean_stress_sensitivity,
            stress_ratio=curve.stress_ratio,
            standard=curve.standard,
            source=(
                f"{curve.source}; restated from P(f) = {curve.failure_probability:g} to "
                f"{probability:g} using the declared scatter, via {self.version}"
            ),
        )

    # -- damage -----------------------------------------------------------

    def damage(self, collective: Collective, curve: SNCurve) -> tuple[float, ...]:
        require()
        if not collective.blocks:
            return ()
        frame = _to_frame(collective)
        damages = _to_woehler(curve).fatigue.damage(frame.load_collective)
        return tuple(float(d) for d in _np.asarray(damages, dtype=float))

    # -- mean stress ------------------------------------------------------

    def correct_mean_stress(
        self, collective: Collective, *, m: float, m2: float | None
    ) -> Collective:
        """FKM-Goodman: every block moved onto the R = −1 line of the Haigh diagram.

        The transformation is Δσ_eq = Δσ + 2·M·σ_m for R ≤ 0, and uses the
        shallower slope M2 (defaulting to M/3, the FKM convention) above R = 0
        where the whole cycle is tensile. Both slopes come from the material's
        declared mean-stress sensitivity; neither is guessed here.
        """
        require()
        if not collective.blocks:
            return collective
        parameters = {"M": float(m)}
        if m2 is not None:
            parameters["M2"] = float(m2)
        haigh = _HaighDiagram.fkm_goodman(_pd.Series(parameters))
        transformed = haigh.transform(_to_frame(collective), -1.0)
        return _from_frame(transformed, collective)


def _to_woehler(curve: SNCurve) -> Any:
    """Our `SNCurve` as the pandas Series pyLife's accessors validate against."""
    data: dict[str, float] = {
        "k_1": float(curve.slope_k1),
        "ND": float(curve.knee_cycles),
        "SD": float(curve.knee_amplitude_mpa),
        "failure_probability": float(curve.failure_probability),
    }
    if curve.slope_k2 is not None:
        data["k_2"] = float(curve.slope_k2)
    if curve.scatter_tn is not None:
        data["TN"] = float(curve.scatter_tn)
    if curve.scatter_ts is not None:
        data["TS"] = float(curve.scatter_ts)
    return _pd.Series(data)


def _to_frame(collective: Collective) -> Any:
    """Amplitude → range. The one place the factor of two lives on the way out."""
    return _pd.DataFrame(
        {
            "range": [b.range_mpa for b in collective.blocks],
            "mean": [b.mean_mpa for b in collective.blocks],
            "cycles": [b.cycles for b in collective.blocks],
        }
    )


def _from_frame(frame: Any, like: Collective) -> Collective:
    """Range → amplitude. The one place the factor of two lives on the way back."""
    # Columns by name, not `itertuples`: a namedtuple silently renames any field
    # that collides with one of its own methods, and `count` — which a collective
    # frame would carry under a different naming convention — is one of them.
    ranges = _np.asarray(frame["range"], dtype=float)
    means = _np.asarray(frame["mean"], dtype=float)
    counts = _np.asarray(frame["cycles"], dtype=float)
    blocks = tuple(
        CycleBlock(amplitude_mpa=float(r) / 2.0, mean_mpa=float(m), cycles=float(n))
        for r, m, n in zip(ranges, means, counts, strict=True)
    )
    return Collective(
        blocks=blocks,
        basis=like.basis,
        sign=like.sign,
        source=like.source,
        counting_method=like.counting_method,
        residue_mpa=like.residue_mpa,
    )


def default_backend() -> FatigueBackend:
    """The backend an assessment uses when it is not handed one.

    A function rather than a module-level singleton so that importing this
    module never constructs anything, which is what lets the package import on a
    machine with no pyLife.
    """
    return PyLifeBackend()


__all__ = [
    "FatigueBackend",
    "PyLifeBackend",
    "available",
    "default_backend",
    "import_error",
    "library_version",
    "require",
]
