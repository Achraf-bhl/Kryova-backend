"""The request and response for a fatigue check on a finished run (master plan E8.6).

One solved load, scaled by a signal of dimensionless multiples, read at one node,
assessed against one S-N curve. Every number an engineer chooses is a field with
a `source`, and none has a default a reader could mistake for a recommendation:
the library refuses a missing factor as `UNMEASURED`, and this schema passes that
refusal through rather than filling the gap.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SNCurveSpec(BaseModel):
    """A two-slope Basquin curve, as `app.fatigue.material.SNCurve` takes it."""

    slope_k1: float = Field(gt=0)
    knee_cycles: float = Field(gt=0)
    knee_amplitude_mpa: float = Field(gt=0)
    source: str = Field(min_length=1)
    slope_k2: float | None = Field(default=None, gt=0)
    scatter_tn: float | None = Field(default=None, gt=0)
    failure_probability: float = Field(default=0.5, gt=0, lt=1)
    mean_stress_sensitivity: float | None = Field(default=None, ge=0)


class FactorSpec(BaseModel):
    name: str = Field(min_length=1)
    value: float = Field(gt=0)
    source: str = Field(min_length=1)


class DirectionSpec(BaseModel):
    vector: tuple[float, float, float]
    reason: str = Field(min_length=1)


class FatigueRequest(BaseModel):
    """Where to read, how the load varies, and what to assess it against."""

    #: Exactly one of `node` and `point_mm`. A point is resolved to the nearest
    #: node and the distance is reported, never hidden.
    node: int | None = Field(default=None, ge=0)
    point_mm: tuple[float, float, float] | None = None
    scalar: Literal["principal", "signed_von_mises", "component"] = "principal"
    direction: DirectionSpec | None = None
    #: Multiples of the solved load, one block of the duty cycle. At least three samples.
    signal: list[float] = Field(min_length=3, max_length=100_000)
    signal_source: str = Field(min_length=1)
    curve: SNCurveSpec
    factors: list[FactorSpec] = Field(default_factory=list)
    design_life_blocks: float = Field(gt=0)
    damage_limit: float = Field(default=1.0, gt=0, le=1)
    mean_stress_policy: Literal["correct", "declared_irrelevant"] = "correct"
    mean_stress_justification: str = ""
    location: str = ""

    @model_validator(mode="after")
    def _one_place(self) -> FatigueRequest:
        if (self.node is None) == (self.point_mm is None):
            raise ValueError(
                "Give exactly one of node and point_mm: the node to read, or a point "
                "whose nearest node is read (its distance is reported)."
            )
        return self


class FatigueRead(BaseModel):
    """The history that was assessed, and the assessment, with everything it rests on."""

    simulation_id: str
    node: int
    position_mm: tuple[float, float, float] | None
    #: Distance from `point_mm` to the node read; None when a node was asked for.
    distance_mm: float | None
    history_mpa: list[float]
    history_source: str
    notes: list[str]
    outcome: str
    summary: str
    #: `DamageResult.to_payload()`: the damage and life with their provenance
    #: sidecar, the method, assumptions, inputs and what was missing.
    assessment: dict[str, object]
    #: The run's own result block, so a reader sees the convergence verdict of
    #: the stress this rests on beside the damage.
    result: dict[str, object] | None
    #: Always present: nothing here is validation (`standards.NOT_VALIDATED`).
    statement: str


__all__ = [
    "DirectionSpec",
    "FactorSpec",
    "FatigueRead",
    "FatigueRequest",
    "SNCurveSpec",
]
