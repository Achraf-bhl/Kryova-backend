"""What a part costs to make, as an estimate that says what it rests on -- master plan 13.3.

Four components, the ones the task names: **material, process, tooling and assembly**.
Each is arithmetic on rates, and every rate is the caller's with its source (a supplier
price list, a shop's hourly rate, a CAM estimate, a quotation). **No rate has a default
and none is shipped here**: a price per kilogram of 6061 is a fact about a supplier on a
date, and a number typed into this module would be a remembered price with the product's
authority behind it.

The arithmetic, stated so it can be checked by hand:

* material = finished mass × stock factor × price per kg. The stock factor is kilograms of
  stock bought per kilogram of finished part (1 for a near-net casting, several for a part
  hogged from bar). The mass is the kernel's measured mass, never a volume times a guessed
  density.
* process = cycle minutes / 60 × machine rate + setup minutes / 60 × machine rate ÷ batch.
* tooling = tooling cost ÷ the number of parts it is amortised over.
* assembly = assembly minutes / 60 × labour rate.

**A component nobody gave inputs for is unknown, not zero.** A part with no tooling says so
with an explicit zero and a source ("machined from stock, no dedicated tooling"), because an
omitted input and a real zero are opposite claims. If any component is unknown the total is
unavailable, with every missing input named, and a cost budget checked against it comes back
`UNMEASURED`. A total is an **estimate**, so its provenance is `APPROXIMATED`, never
measured.

`CostTools.cost` is the method `app.design.machine_checks.CostBudget` has been waiting on
since E5 task 1, so a design's cost budget is now checkable wherever a caller supplies rates.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.design.machine_checks import ToolUnavailable
from app.rules.errors import RuleError, SourceError


@dataclass(frozen=True)
class Rate:
    """One input to the estimate, with where it came from."""

    value: float
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise SourceError(
                "A cost input needs a source: a price list, a quotation, a shop rate or a CAM "
                "estimate, with its date if it has one."
            )
        if not math.isfinite(self.value) or self.value < 0.0:
            raise RuleError(f"A cost input is finite and not negative; got {self.value!r}.")


@dataclass(frozen=True)
class CostInputs:
    currency: str
    batch_size: int
    material_price_per_kg: Rate | None = None
    stock_factor: Rate | None = None
    cycle_minutes: Rate | None = None
    setup_minutes: Rate | None = None
    machine_rate_per_hour: Rate | None = None
    tooling_cost: Rate | None = None
    tooling_amortised_over_parts: Rate | None = None
    assembly_minutes: Rate | None = None
    labour_rate_per_hour: Rate | None = None

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise RuleError("A cost estimate needs a currency, e.g. 'EUR'.")
        if self.batch_size < 1:
            raise RuleError(
                f"A batch of {self.batch_size} parts spreads its setup over nothing. Give at "
                "least 1."
            )
        if self.stock_factor is not None and self.stock_factor.value < 1.0:
            raise RuleError(
                f"A stock factor of {self.stock_factor.value} buys less stock than the part "
                "weighs. It is kilograms bought per kilogram finished, at least 1."
            )
        if (
            self.tooling_amortised_over_parts is not None
            and self.tooling_amortised_over_parts.value <= 0.0
        ):
            raise RuleError("Tooling is amortised over a positive number of parts.")


@dataclass(frozen=True)
class CostLine:
    name: str
    value: float | None
    formula: str
    missing: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class CostEstimate:
    currency: str
    lines: tuple[CostLine, ...]
    mass_kg: float
    mass_source: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(f"{line.name}: {m}" for line in self.lines for m in line.missing)

    @property
    def total(self) -> float | None:
        if self.missing:
            return None
        return sum(line.value for line in self.lines if line.value is not None)

    def to_payload(self) -> dict[str, Any]:
        """`cost.total` and one entry per line, with the provenance sidecar."""
        from app.kernel import provenance

        payload: dict[str, Any] = {
            "currency": self.currency,
            "lines": [
                {
                    "name": line.name,
                    "value": line.value,
                    "formula": line.formula,
                    "missing": list(line.missing),
                    "sources": list(line.sources),
                }
                for line in self.lines
            ],
            "mass_kg": self.mass_kg,
            "mass_source": self.mass_source,
        }
        total = self.total
        if total is None:
            provenance.attach(
                payload, "total", provenance.unavailable("missing: " + "; ".join(self.missing))
            )
        else:
            payload["total"] = total
            provenance.attach(
                payload,
                "total",
                provenance.approximated(
                    "estimate: material + process + tooling + assembly from the stated rates"
                ),
            )
        return payload


def _need(label: str, rate: Rate | None, missing: list[str], sources: list[str]) -> float:
    if rate is None:
        missing.append(label)
        return 0.0
    sources.append(f"{label}: {rate.source}")
    return rate.value


def estimate(inputs: CostInputs, *, mass_kg: float, mass_source: str) -> CostEstimate:
    """The four components, each with its formula, sources and anything missing."""
    if not math.isfinite(mass_kg) or mass_kg < 0.0:
        raise RuleError(f"A part's mass is finite and not negative; got {mass_kg!r}.")
    if not mass_source.strip():
        raise SourceError(
            "Say where the mass came from: the kernel's measured mass, or the roll-up."
        )

    lines: list[CostLine] = []

    missing: list[str] = []
    sources: list[str] = []
    price = _need("material price per kg", inputs.material_price_per_kg, missing, sources)
    stock = _need("stock factor", inputs.stock_factor, missing, sources)
    lines.append(
        CostLine(
            "material",
            None if missing else mass_kg * stock * price,
            f"{mass_kg:.6g} kg x stock factor x price per kg",
            tuple(missing),
            tuple(sources),
        )
    )

    missing, sources = [], []
    cycle = _need("cycle minutes", inputs.cycle_minutes, missing, sources)
    setup = _need("setup minutes", inputs.setup_minutes, missing, sources)
    rate = _need("machine rate per hour", inputs.machine_rate_per_hour, missing, sources)
    lines.append(
        CostLine(
            "process",
            None if missing else cycle / 60.0 * rate + setup / 60.0 * rate / inputs.batch_size,
            f"cycle/60 x rate + setup/60 x rate / batch of {inputs.batch_size}",
            tuple(missing),
            tuple(sources),
        )
    )

    missing, sources = [], []
    tooling = _need("tooling cost", inputs.tooling_cost, missing, sources)
    over = _need(
        "tooling amortised over parts", inputs.tooling_amortised_over_parts, missing, sources
    )
    lines.append(
        CostLine(
            "tooling",
            None if missing else tooling / over,
            "tooling cost / parts it is amortised over",
            tuple(missing),
            tuple(sources),
        )
    )

    missing, sources = [], []
    minutes = _need("assembly minutes", inputs.assembly_minutes, missing, sources)
    labour = _need("labour rate per hour", inputs.labour_rate_per_hour, missing, sources)
    lines.append(
        CostLine(
            "assembly",
            None if missing else minutes / 60.0 * labour,
            "assembly minutes/60 x labour rate",
            tuple(missing),
            tuple(sources),
        )
    )

    return CostEstimate(
        currency=inputs.currency,
        lines=tuple(lines),
        mass_kg=mass_kg,
        mass_source=mass_source,
        notes=(
            "An estimate from stated rates, not a quotation: overhead, margin, scrap, "
            "finishing, inspection and freight are not in it unless a rate carries them.",
        ),
    )


@dataclass(frozen=True)
class CostTools:
    """The `cost` half of `app.design.machine_checks.MachineTools`."""

    inputs: CostInputs
    #: The subject's measured mass in kg, and where it came from.
    mass_of: Callable[[Any], tuple[float, str]]

    def cost(self, subject: Any, currency: str) -> float:
        if currency != self.inputs.currency:
            raise ToolUnavailable(
                f"the budget is in {currency} and the rates are in {self.inputs.currency}; "
                "nothing here converts currency, so restate one of them."
            )
        mass, source = self.mass_of(subject)
        answer = estimate(self.inputs, mass_kg=mass, mass_source=source)
        total = answer.total
        if total is None:
            raise ToolUnavailable(
                "the cost estimate is missing " + "; ".join(answer.missing) + ". Give each "
                "with its source, or an explicit zero with a source where it does not apply."
            )
        return total


__all__ = ["CostEstimate", "CostInputs", "CostLine", "CostTools", "Rate", "estimate"]
