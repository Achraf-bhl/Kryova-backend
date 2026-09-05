"""Machine-checkable claims about the part, re-run on every regeneration.

C3 in the roadmap: unit testing for geometry. *"the pivot centre is 25 mm from
the datum"*, *"minimum wall ≥ 3 mm"*, *"mass ≤ 4.2 kg"*. A design that carries
its own assertions can be changed by something that is not a person and still be
known to be right afterwards, and that is the property autonomous iteration
needs — C4's loop is only safe because this module is what tells it whether the
last attempt worked.

**An assertion is evaluated against a measurement payload, not against CATIA.**
The measuring is somebody else's job — `catia_measure` returns mass, volume,
bounding box and centre of gravity in one call, and a `BuildReport`'s last
result usually already holds them. What arrives here is a plain mapping of
numbers. That keeps every assertion testable with no seat, no bridge and no
Windows, which is the same reason the solver tests run offline, and it means the
same assertion can be checked against a real measurement, a mock one, or a
simulation result without knowing the difference.

**A measurement that is missing is not a pass.** Three outcomes, not two:
`PASSED`, `FAILED`, and `UNMEASURED`. This is the whole reason the module is
worth having. An assertion suite that silently skips what it could not measure
reports green on a part nobody checked, and the roadmap's own warning about
un-converged numbers stated with confidence applies exactly as well to a wall
thickness nobody read. The recoveries differ too — a failure means change the
design, an unmeasured claim means go and measure it — so they are never merged.

**A bound may be a formula over the design's parameters.** `mass_kg <=
"=target_mass_kg"` travels with the design: change the target and the assertion
moves with it, instead of being a number that silently stops matching the design
it was written for. Formulas go through the same evaluator and the same
dimensional checking as everything else in `params`, so a bound that adds a
length to an angle is refused here rather than passing quietly.

**Whether the number was approximate is carried through, per number.** The mock
computes mass from a bounding box less the swept volume of each cut and says so,
and a report that let that pass as a measured fact would be the mock's warning
defeated one layer up. A kernel payload is *mixed* — mass integrated exactly,
wall thickness ray cast on a grid — so the caveat is read from
`app.kernel.provenance`'s per-path sidecar where the payload carries one, and
falls back to the payload-wide `approximate` flag where it does not. A report is
approximate when an assertion actually read an approximate number, not merely
when one was present.

**A measurement that is missing says why it is missing.** Where the backend tried
and could not, it records the reason — "this shape encloses no solid, so it has
no volume" — and that is what the unmeasured result quotes. Where the path is
documented in `app.kernel.contract` but absent, the message says which scan
produces it. Only when the path is in no contract at all does it fall back to
listing what *is* available, which is the message that used to be given in every
case.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.design import spec as spec_module
from app.design.errors import SpecError
from app.design.params import Quantity, ResolvedParameters, evaluate

#: Comparisons an assertion may make. Deliberately small: these are claims about
#: a number, and an assertion language that grows a general expression evaluator
#: on the left-hand side becomes a second, worse spec language.
COMPARISONS: Final[frozenset[str]] = frozenset({"<=", ">=", "<", ">", "==", "!="})

#: A key the daemon sets when the number it returned was not measured off a real
#: B-rep. Read rather than assumed so a mock result cannot be reported as fact.
APPROXIMATE_KEY: Final = "approximate"

_INDEX_RE: Final = re.compile(r"^(?P<name>[^\[\]]+)\[(?P<index>\d+)\]$")


class Outcome(StrEnum):
    """How an assertion came out.

    `UNMEASURED` is not a soft failure and not a skip. It means the claim was
    not tested, and a suite containing one has not verified the part.
    """

    PASSED = "passed"
    FAILED = "failed"
    UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class Assertion:
    """One checkable claim about the built part.

    `measure` is a path into the measurement payload — `mass_kg`,
    `bounding_box_mm.size[2]`, `center_of_gravity_mm[0]`. Paths rather than a
    fixed vocabulary because the payload is whatever the measuring tool returned,
    and a fixed vocabulary would have to be widened every time a tool learns to
    report something new.
    """

    name: str
    measure: str
    comparison: str
    bound: float | str

    #: Slack, in the unit of the measurement. A geometric kernel and a mesher do
    #: not produce exact decimals, so an equality with no tolerance is a claim
    #: that will fail on a part that is right. Defaults to zero for the
    #: inequalities, where it usually is not wanted, and must be set explicitly
    #: for `==`, which is refused without one.
    tolerance: float = 0.0

    #: Why this claim exists — a requirement, a standard, a clearance someone
    #: measured once. Carried into the failure message, because "mass_kg <= 4.2"
    #: with a reason attached is arguable and without one is just a number.
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise SpecError("An assertion needs a name; it is what a failure is reported under.")
        if not self.measure or not str(self.measure).strip():
            raise SpecError(
                f"{self.name}: needs something to measure, e.g. 'mass_kg' or "
                "'bounding_box_mm.size[2]'."
            )
        if self.comparison not in COMPARISONS:
            allowed = ", ".join(sorted(COMPARISONS))
            raise SpecError(
                f"{self.name}: {self.comparison!r} is not a comparison this understands. "
                f"Use one of: {allowed}."
            )
        if self.tolerance < 0:
            raise SpecError(
                f"{self.name}: a negative tolerance ({self.tolerance}) would make the "
                "assertion stricter than exact, which is not a thing. Use a positive "
                "slack, or zero."
            )
        if self.comparison == "==" and self.tolerance == 0:
            raise SpecError(
                f"{self.name}: an exact equality on a measured number will fail on a part "
                "that is correct — a kernel does not return round decimals. Give it a "
                "tolerance, e.g. tolerance=0.01."
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "measure": self.measure,
            "comparison": self.comparison,
            "bound": self.bound,
        }
        if self.tolerance:
            out["tolerance"] = self.tolerance
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Assertion:
        unknown = set(data) - {"name", "measure", "comparison", "bound", "tolerance", "note"}
        if unknown:
            raise SpecError(
                f"Assertion {data.get('name')!r} carries unknown keys {sorted(unknown)}. A "
                "key this build does not understand is one it would silently drop."
            )
        return cls(
            name=str(data["name"]),
            measure=str(data["measure"]),
            comparison=str(data["comparison"]),
            bound=data["bound"],
            tolerance=float(data.get("tolerance") or 0.0),
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class AssertionResult:
    """One assertion, checked."""

    assertion: Assertion
    outcome: Outcome
    measured: float | None = None
    expected: float | None = None

    #: Why it could not be evaluated, when it could not be. Empty otherwise.
    reason: str = ""

    #: True when the number checked was not measured off real geometry.
    approximate: bool = False

    @property
    def name(self) -> str:
        return self.assertion.name

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASSED

    @property
    def gap(self) -> float | None:
        """How far the wrong side of the bound it landed, or None if it passed.

        The number a correction loop actually needs: "3.1 kg over" is
        actionable, "failed" is not.
        """
        if self.outcome is not Outcome.FAILED or self.measured is None or self.expected is None:
            return None
        return self.measured - self.expected

    def __str__(self) -> str:
        a = self.assertion
        if self.outcome is Outcome.UNMEASURED:
            return f"{a.name}: not checked — {self.reason}"
        measured = "?" if self.measured is None else f"{self.measured:g}"
        expected = "?" if self.expected is None else f"{self.expected:g}"
        head = f"{a.name}: {a.measure} = {measured}, needs {a.comparison} {expected}"
        if self.outcome is Outcome.PASSED:
            return f"{head} — passed" + (" (approximate)" if self.approximate else "")
        gap = self.gap
        over = f" by {abs(gap):g}" if gap is not None else ""
        tail = f" — FAILED{over}"
        if a.note:
            tail += f". {a.note}"
        return head + tail

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "outcome": str(self.outcome),
            "measure": self.assertion.measure,
            "comparison": self.assertion.comparison,
        }
        if self.measured is not None:
            out["measured"] = self.measured
        if self.expected is not None:
            out["expected"] = self.expected
        if self.gap is not None:
            out["gap"] = self.gap
        if self.reason:
            out["reason"] = self.reason
        if self.approximate:
            out["approximate"] = True
        return out


@dataclass(frozen=True)
class AssertionReport:
    """Every claim about one part, checked at once.

    Truthy only when everything was checked *and* everything passed. An
    unmeasured claim makes the report falsey, which is the conservative reading
    and the correct one: the part has not been verified.
    """

    results: tuple[AssertionResult, ...] = ()

    #: True when any number checked came from something that said it was
    #: approximate. Propagated to the report so a caller cannot read `ok`
    #: without the caveat being available beside it.
    approximate: bool = field(default=False)

    @property
    def failed(self) -> tuple[AssertionResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.FAILED)

    @property
    def unmeasured(self) -> tuple[AssertionResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.UNMEASURED)

    @property
    def passed(self) -> tuple[AssertionResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.PASSED)

    @property
    def ok(self) -> bool:
        return bool(self.results) and not self.failed and not self.unmeasured

    def __bool__(self) -> bool:
        return self.ok

    def __iter__(self) -> Any:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def summary(self) -> str:
        if not self.results:
            return "No assertions to check."
        bits = [f"{len(self.passed)}/{len(self.results)} passed"]
        if self.failed:
            bits.append(f"{len(self.failed)} failed")
        if self.unmeasured:
            bits.append(f"{len(self.unmeasured)} not measurable")
        line = ", ".join(bits) + "."
        detail = [str(r) for r in self.results if r.outcome is not Outcome.PASSED]
        if self.approximate and self.ok:
            line += " Numbers are approximate — not measured off real geometry."
        return line + ("\n" + "\n".join(detail) if detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "unmeasured": len(self.unmeasured),
            "approximate": self.approximate,
            "results": [result.to_dict() for result in self.results],
        }


def check_assertions(
    assertions: Iterable[Assertion],
    measurements: Mapping[str, Any],
    *,
    parameters: ResolvedParameters | None = None,
) -> AssertionReport:
    """Check every assertion against one measurement payload.

    Checks all of them rather than stopping at the first failure, unlike the
    plan executor. The reasoning is opposite and both are right: a plan's calls
    stand on each other, so everything after the first failure is noise, while
    assertions are independent claims and knowing that three of them failed
    together is what tells a correction loop it is looking at one cause rather
    than three.
    """
    results = [
        _check_one(assertion, measurements, parameters) for assertion in assertions
    ]
    # Derived from the results rather than from the payload: a report is approximate when
    # a claim was actually *checked* against an approximate number. A payload full of ray
    # cast scans that no assertion read does not make the report approximate, and an
    # unmeasured claim cannot make it approximate either — nothing was read at all.
    return AssertionReport(
        results=tuple(results),
        approximate=any(result.approximate for result in results),
    )


def _check_one(
    assertion: Assertion,
    measurements: Mapping[str, Any],
    parameters: ResolvedParameters | None,
) -> AssertionResult:
    try:
        expected = _resolve_bound(assertion, parameters)
    except SpecError as exc:
        return AssertionResult(
            assertion=assertion,
            outcome=Outcome.UNMEASURED,
            reason=str(exc),
        )

    found = read_measurement(measurements, assertion.measure)
    if found is None:
        return AssertionResult(
            assertion=assertion,
            outcome=Outcome.UNMEASURED,
            expected=expected,
            reason=_why_missing(measurements, assertion.measure),
        )

    passed = _compare(found, assertion.comparison, expected, assertion.tolerance)
    return AssertionResult(
        assertion=assertion,
        outcome=Outcome.PASSED if passed else Outcome.FAILED,
        measured=found,
        expected=expected,
        approximate=_is_approximate(measurements, assertion.measure),
    )


def _is_approximate(measurements: Mapping[str, Any], path: str) -> bool:
    """Was *this* number approximate — not merely somewhere in the payload?

    The payload-wide `approximate` flag came first and is coarse: it marks every number
    in a payload once any of them is estimated, which was right when the only source was
    the CATIA mock estimating the whole thing. A kernel payload is mixed — mass is
    integrated exactly, wall thickness is ray cast — so per-path provenance is consulted
    first and the global flag is the fallback for payloads that carry none.
    """
    basis = _provenance().basis_of(measurements, path)
    if basis is _provenance().Basis.APPROXIMATED:
        return True
    if basis is _provenance().Basis.MEASURED:
        # An exactly measured number is not tainted by an approximation elsewhere in the
        # same payload; saying otherwise would make every report on a scanned part read
        # as estimated, and the caveat would stop meaning anything.
        return False
    return bool(measurements.get(APPROXIMATE_KEY))


def _provenance() -> Any:
    """`app.kernel.provenance`, imported on use rather than at module load.

    **Deliberate, not an oversight.** Importing it at the top would execute
    `app/kernel/__init__.py`, which pulls in the OCCT binding and with it ~166 MB of
    `OCP` — turning this package's offline-in-under-a-second test run into something
    nobody wants to iterate on. `app/design/` depends on the *vocabulary* of provenance,
    never on a kernel being installed, and this is what keeps those two facts compatible.
    After the first call it is a `sys.modules` lookup.
    """
    from app.kernel import provenance

    return provenance


def _why_missing(measurements: Mapping[str, Any], path: str) -> str:
    """The most useful thing that can be said about a measurement that is not there.

    Three cases, in descending order of how much the payload knows:

    1. The backend **tried and could not** — it recorded an `UNAVAILABLE` provenance
       entry with a reason, which is quoted verbatim. This is the case worth having:
       "the shape encloses no solid, so it has no volume" is actionable, and "nothing
       reports volume_mm3" is not.
    2. The path is **documented but absent**, so the scan that produces it was never run.
       The contract can say what it is and what it is for.
    3. The path is **not in the contract at all** — most likely a typo or a spelling from
       another CAD system, so the available paths are listed.
    """
    from app.kernel import contract

    reason = _provenance().reason_for(measurements, path)
    if reason:
        return f"{path} could not be measured: {reason}"

    documented = contract.entry(path)
    if documented is not None:
        return (
            f"nothing in this measurement reports {path!r}, and no scan that produces it "
            f"was run. {contract.describe(path)}"
        )

    available = ", ".join(sorted(measurable_paths(measurements))) or "nothing"
    return (
        f"nothing in the measurement reports {path!r}, and it is not a documented "
        f"quantity. Measured here: {available}. Run the tool that reports it — "
        "catia_measure for mass, volume, bounding box and centre of gravity, "
        "catia_analysis_part for wall thickness, draft and curvature — before checking "
        "this."
    )


def _resolve_bound(
    assertion: Assertion, parameters: ResolvedParameters | None
) -> float:
    """The number on the right-hand side, evaluating a formula if that is what it is."""
    bound = assertion.bound
    if isinstance(bound, (int, float)) and not isinstance(bound, bool):
        return float(bound)
    if not isinstance(bound, str):
        raise SpecError(
            f"{assertion.name}: the bound must be a number or an '=expression', "
            f"got {type(bound).__name__}."
        )
    if not spec_module.is_expression(bound):
        raise SpecError(
            f"{assertion.name}: {bound!r} is neither a number nor a formula. A formula "
            "over the design's parameters starts with '=', e.g. '=target_mass_kg'."
        )
    if parameters is None:
        raise SpecError(
            f"{assertion.name}: its bound {bound!r} reads the design's parameters, but "
            "none were supplied to check it against. Pass the resolved parameters from "
            "the compiled plan."
        )
    source = spec_module.expression_source(bound)
    computed: Quantity = evaluate(source, parameters.values)
    return computed.value


def _compare(measured: float, comparison: str, expected: float, tolerance: float) -> bool:
    """Apply the comparison, with tolerance always favouring the part.

    Tolerance widens what is acceptable in every case rather than shifting the
    bound in a fixed direction, because it represents "we cannot measure more
    finely than this", not "we will accept a bit worse".
    """
    if comparison == "<=":
        return measured <= expected + tolerance
    if comparison == ">=":
        return measured >= expected - tolerance
    if comparison == "<":
        return measured < expected + tolerance
    if comparison == ">":
        return measured > expected - tolerance
    if comparison == "==":
        return abs(measured - expected) <= tolerance
    return abs(measured - expected) > tolerance


# -- reading a measurement payload -------------------------------------------


def read_measurement(payload: Mapping[str, Any], path: str) -> float | None:
    """Follow a dotted/indexed path into a payload and return a number.

    Returns None when the path is not there, or is there and is not a number.
    Both are the same outcome for an assertion — the claim could not be checked
    — and distinguishing them in the return type would push a decision onto
    every caller that none of them would make differently.
    """
    current: Any = payload
    for segment in path.split("."):
        match = _INDEX_RE.match(segment)
        index: int | None = None
        if match:
            segment = match.group("name")
            index = int(match.group("index"))
        if isinstance(current, Mapping):
            if segment not in current:
                return None
            current = current[segment]
        else:
            return None
        if index is not None:
            if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
                return None
            if index >= len(current):
                return None
            current = current[index]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    return float(current)


def measurable_paths(payload: Mapping[str, Any], prefix: str = "") -> tuple[str, ...]:
    """Every path in a payload that holds a number.

    Used to make a missing measurement's message actionable: "there is no
    `wall_mm` here, but there is `mass_kg`, `volume_mm3`, ..." is a message
    someone can act on, and "not found" is one they cannot.
    """
    found: list[str] = []
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            found.append(path)
        elif isinstance(value, Mapping):
            found.extend(measurable_paths(value, prefix=f"{path}."))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            found.extend(
                f"{path}[{index}]"
                for index, item in enumerate(value)
                if isinstance(item, (int, float)) and not isinstance(item, bool)
            )
    return tuple(found)


__all__ = [
    "APPROXIMATE_KEY",
    "COMPARISONS",
    "Assertion",
    "AssertionReport",
    "AssertionResult",
    "Outcome",
    "check_assertions",
    "measurable_paths",
    "read_measurement",
]
