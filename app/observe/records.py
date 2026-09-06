"""What a measurement *is* here: a span, a distribution, a summary.

Deliberately three small frozen dataclasses and no formatting, no clock and no
global state. Everything else in the package produces or consumes these, which
is what lets the report layer, the queue meter and the span recorder share one
vocabulary instead of three.

Two decisions in here that look like details and are not.

**A distribution over an empty sample is `None`, never a row of zeros.** A
`min_seconds` of `0.0` computed from nothing is a number an operator will read
as "it was instant". `Distribution.of` refuses an empty sequence and the callers
carry `Distribution | None`, so "we have no samples" cannot be mistaken for "the
samples were fast". This is the same rule `app.design.assertions` applies to a
measurement it could not take.

**Percentiles are order statistics, not estimates.** `p95_seconds` is the
nearest-rank value — `sorted[ceil(0.95n) - 1]` — so over nineteen samples it is
simply the maximum, and it says so rather than interpolating a number the sample
cannot support. Interpolated percentiles over a handful of solves are the kind
of plausible-looking wrong number this project treats as worse than none.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

#: Fields whose values are summed across a summary's spans. Anything numeric is
#: summable; a string field (a digest, a format name) is carried on the span and
#: deliberately not aggregated, because summing labels produces nonsense.
_SUMMABLE: Final = (int, float)

_EMPTY_FIELDS: Final[Mapping[str, Any]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Span:
    """One thing that ran, how long it took, and whether it worked.

    `fields` is whatever the call site chose to record about *what it was
    working on* — node count, byte count, iteration number, part count. It is
    the difference between "meshing took 40 seconds" and "meshing 1.2 M nodes
    took 40 seconds", and only the second one answers "why was that slow".

    `ok` is false for a span whose body raised, and `failure` names what raised.
    A failed span is still a recorded span: the run that blew up after nine
    minutes is exactly the one somebody needs the timing for, and dropping it
    would make the arithmetic on the successes look better than the system is.
    """

    name: str
    seconds: float
    ok: bool = True
    failure: str = ""
    fields: Mapping[str, Any] = _EMPTY_FIELDS
    #: Nesting depth within the collection: 0 for a span with no open parent.
    depth: int = 0
    parent: str | None = None
    #: Wall-clock start, for ordering a trace a human reads. Durations are
    #: measured with `perf_counter` and never with this.
    started_at: float = 0.0
    sequence: int = 0
    thread: str = ""

    def __post_init__(self) -> None:
        if self.seconds < 0.0:
            raise ValueError(f"a span cannot last {self.seconds} seconds")


@dataclass(frozen=True, slots=True)
class Distribution:
    """Durations over a set of spans. Never constructed from nothing."""

    count: int
    total_seconds: float
    min_seconds: float
    median_seconds: float
    p95_seconds: float
    max_seconds: float

    @property
    def mean_seconds(self) -> float:
        return self.total_seconds / self.count

    @property
    def p95_is_the_maximum(self) -> bool:
        """True when the sample is too small for p95 to mean anything else.

        Nineteen samples or fewer put the nearest rank on the last element. A
        dashboard that prints p95 without this is quoting the max under a name
        that suggests a tail was measured.
        """
        return self.count < 20

    @classmethod
    def of(cls, seconds: Sequence[float]) -> Distribution:
        if not seconds:
            raise ValueError("a distribution needs at least one sample")
        ordered = sorted(seconds)
        n = len(ordered)
        return cls(
            count=n,
            total_seconds=math.fsum(ordered),
            min_seconds=ordered[0],
            median_seconds=ordered[(n - 1) // 2],
            p95_seconds=ordered[max(0, math.ceil(0.95 * n) - 1)],
            max_seconds=ordered[-1],
        )


@dataclass(frozen=True, slots=True)
class Summary:
    """Every span of one name, rolled up.

    `totals` sums the numeric fields the spans carried, which is what turns a
    pile of spans into throughput: bytes written, nodes meshed, iterations run.

    `incomplete` is set when the recorder discarded spans of this name because
    its buffer filled. The counts below are then a floor, not a count, and the
    report refuses to present them as measured — see `app.observe.report`.
    """

    name: str
    durations: Distribution
    failures: int
    totals: Mapping[str, float] = _EMPTY_FIELDS
    incomplete: bool = False
    dropped: int = 0

    @property
    def count(self) -> int:
        return self.durations.count

    @property
    def successes(self) -> int:
        return self.count - self.failures

    @property
    def success_rate(self) -> float:
        return self.successes / self.count

    @classmethod
    def of(cls, name: str, spans: Sequence[Span], dropped: int = 0) -> Summary:
        totals: dict[str, float] = {}
        for span in spans:
            for key, value in span.fields.items():
                if isinstance(value, _SUMMABLE) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0.0) + float(value)
        return cls(
            name=name,
            durations=Distribution.of([s.seconds for s in spans]),
            failures=sum(1 for s in spans if not s.ok),
            totals=MappingProxyType(dict(sorted(totals.items()))),
            incomplete=dropped > 0,
            dropped=dropped,
        )


@dataclass(frozen=True, slots=True)
class Note:
    """A free-standing observation attached to a collection.

    Used where something worth knowing is not a duration — "CalculiX is not
    installed on this machine", "the gmsh lock was contended by three threads".
    Kept as data rather than a log line so a report can carry it.
    """

    text: str
    fields: Mapping[str, Any] = field(default_factory=dict)
