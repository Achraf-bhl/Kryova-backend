"""Interface contracts — the boundary between two things, made checkable.

Master plan 14.2 and 14.3, and the reason Phase 14 is called the architecturally most
important era-V phase. Two sub-assemblies designed separately — by two engineers, two
agents, or the same agent a week apart — have to meet: a bolt pattern, a mating face, an
envelope nobody may grow into, a mass budget, a load path. The contract is where that
meeting is written down, and **it is written as an assertion, not as a new mechanism.**

**Nothing here invents a second verdict type.** A claim on an interface is an
`app.design.assertions.Assertion`, checked by `check_assertions`, reported as `PASSED`,
`FAILED` or `UNMEASURED`. That matters more than it looks: an interface whose clearance
could not be measured must come back `UNMEASURED` and not `PASSED`, and the one place in
this codebase that gets that right is the one that already exists. A second verdict
enum would be a second place for the unmeasured case to be forgotten.

**The valuable property is that a violation names both parties.** A failed assertion on
a part says `mass_kg = 4.9, needs <= 4.2`. A failed assertion on an *interface* says
which two components can no longer be built together, which is the sentence somebody
acts on — and it is what makes `affected()` useful: change the swingarm, and the answer
is not "something broke", it is "`pivot_fit` is violated, and the counterparty is the
frame". `Violation.counterparty` exists for exactly that call.

**A contract is also a compilable spec fragment (14.3).** `Interface.parameters` is an
`app.design.params.ParameterSet` — the pivot diameter, the bolt circle, the envelope —
and `bind_into` merges it into a `DesignSpec` so *both* sides build from the same
numbers rather than from two copies of them. Two copies is the failure this removes:
they agree on the day they are typed and diverge on the day one is edited, and nothing
errors. A side that redeclares an interface parameter with a *different* value is
refused at bind time with both parties named — a compile error at the interface, which
is 14.3's own phrase and is three weeks earlier than the clash it would otherwise
become.

**Party names are resolved through the product graph, not by string matching.** A
contract may name a component (`swingarm`) or an occurrence path
(`bike/rear.1/swingarm.1`). `affected()` given a structure resolves a path to the
components on it, so a change to a bracket deep inside a sub-assembly still reaches a
contract written against the sub-assembly. Given no structure it compares names exactly
and says so — a narrower answer, never a guessed one.

**Measurements from two sides are namespaced, provenance included.** `measurements()`
puts the provider's payload under `provider.` and the consumer's under `consumer.`, so a
claim reads `provider.bore_diameter_mm >= consumer.shaft_diameter_mm`… almost. The
assertion vocabulary compares a measured path against a *bound*, not against a second
path, so the second side enters through the contract's own parameters. What the
namespacing buys is that one claim can read either side's numbers by name. The
provenance sidecar is re-keyed with the same prefixes — miss that and an approximated
provider measurement is silently reported as measured, which is the mock-mass lie one
layer up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.assembly.errors import ContractError
from app.assembly.structure import ProductStructure
from app.design.assertions import (
    Assertion,
    AssertionReport,
    AssertionResult,
    Outcome,
    check_assertions,
)
from app.design.errors import SpecError
from app.design.params import Parameter, ParameterSet
from app.design.spec import DesignSpec

#: Reserved key under which a measurement payload carries its provenance sidecar.
#: Spelled here rather than imported from `app.kernel.provenance` for the reason
#: `app.design.assertions._provenance` records: importing that module executes
#: `app/kernel/__init__.py`, which pulls in ~166 MB of OCP and turns this package's
#: offline-in-milliseconds test run into something nobody iterates on. This package
#: depends on the *vocabulary* of provenance, never on a kernel being installed.
#: `tests/test_assembly_contracts.py` asserts the two spellings are the same string, so
#: a rename there cannot silently orphan this one.
PROVENANCE_KEY: Final = "provenance"

#: The two roles in an interface. The provider *offers* the feature — the hole pattern,
#: the mounting face, the envelope; the consumer *relies* on it. Naming the roles rather
#: than calling them "a" and "b" is what lets a violation message read as a sentence.
PROVIDER: Final = "provider"
CONSUMER: Final = "consumer"


@dataclass(frozen=True)
class Interface:
    """A checkable claim about the boundary between two things.

    `provider` and `consumer` are component names or occurrence paths from a
    `ProductStructure`. They are not validated against a structure here on purpose: a
    contract is written while both sides are still being designed, often before either
    exists as geometry, and refusing it then would push the contract to the end of the
    process — which is where it stops being worth having. `affected()` is where a
    structure enters, and it resolves a party that happens to be an occurrence path;
    nothing in this module refuses a party that names no component, because at the
    moment the contract is written there may not be one yet.
    """

    name: str
    provider: str
    consumer: str

    #: The claims that must hold at this boundary. Ordinary assertions, deliberately.
    claims: tuple[Assertion, ...] = ()

    #: The numbers both sides build from — 14.3's compilable fragment. Empty for a
    #: contract that only checks, non-empty for one that also *defines*.
    parameters: ParameterSet = field(default_factory=ParameterSet)

    #: Why this contract exists. Carried into every violation message, because "the
    #: bearing bore must clear the shaft at temperature" is arguable and a bare number
    #: is not.
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ContractError(
                "An interface needs a name; it is what a violation is reported under."
            )
        for role, party in ((PROVIDER, self.provider), (CONSUMER, self.consumer)):
            if not party or not str(party).strip():
                raise ContractError(
                    f"{self.name}: the {role} is empty. A contract with one side named "
                    "cannot say who else is affected when that side changes, which is "
                    "the whole of what it is for."
                )
        if self.provider == self.consumer:
            raise ContractError(
                f"{self.name}: {self.provider!r} is named as both provider and consumer. "
                "An interface is between two things; a claim about one thing is an "
                "assertion on its design (app.design.assertions)."
            )
        if not self.claims and not len(self.parameters):
            raise ContractError(
                f"{self.name}: a contract with no claims and no shared parameters checks "
                "nothing and defines nothing. Give it at least one assertion "
                "(a clearance, an envelope, a mass budget) or at least one parameter "
                "both sides build from."
            )

    @property
    def parties(self) -> tuple[str, str]:
        return (self.provider, self.consumer)

    def role_of(self, party: str) -> str | None:
        """`provider`, `consumer`, or None if this contract does not name it."""
        if party == self.provider:
            return PROVIDER
        if party == self.consumer:
            return CONSUMER
        return None

    def counterparty(self, party: str) -> str:
        """The *other* side. The one-word answer to "who else does this break?"."""
        if party == self.provider:
            return self.consumer
        if party == self.consumer:
            return self.provider
        raise ContractError(
            f"{self.name} is between {self.provider!r} and {self.consumer!r}; "
            f"{party!r} is neither."
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "provider": self.provider,
            "consumer": self.consumer,
        }
        if self.claims:
            out["claims"] = [claim.to_dict() for claim in self.claims]
        if len(self.parameters):
            out["parameters"] = [
                {
                    "name": p.name,
                    "unit": p.unit.value,
                    **({"value": p.value} if p.value is not None else {}),
                    **({"expression": p.expression} if p.expression is not None else {}),
                    **({"description": p.description} if p.description else {}),
                }
                for p in self.parameters
            ]
        if self.note:
            out["note"] = self.note
        return out


# -- measurement plumbing ----------------------------------------------------


def namespace(payload: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    """One side's measurement payload, with every path prefixed.

    **The provenance sidecar is re-keyed too, and that is not a detail.**
    `assertions._is_approximate` reads `payload["provenance"]["provider.mass_kg"]`; leave
    the sidecar keyed on the bare path and every claim about the provider comes back
    with `approximate=False` — a ray-cast bound reported as an exact measurement, which
    is precisely the mock-mass lie the provenance module exists to prevent. Verified by
    breaking it: `tests/test_assembly_contracts.py` fails on a bare-keyed sidecar.
    """
    if not prefix:
        return dict(payload)
    body = {key: value for key, value in payload.items() if key != PROVENANCE_KEY}
    merged: dict[str, Any] = {prefix: body}
    sidecar = payload.get(PROVENANCE_KEY)
    if isinstance(sidecar, Mapping):
        merged[PROVENANCE_KEY] = {
            f"{prefix}.{path}": dict(record) if isinstance(record, Mapping) else record
            for path, record in sidecar.items()
        }
    return merged


def measurements(
    *,
    provider: Mapping[str, Any] | None = None,
    consumer: Mapping[str, Any] | None = None,
    boundary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One payload a contract's claims can read, from up to three sources.

    `provider` and `consumer` are each side's own measurement, namespaced. `boundary` is
    what was measured *between* them — a clearance, an interference volume, a bolt-hole
    concentricity — and stays at the top level unprefixed, so a claim about the boundary
    reads `minimum_clearance_mm` exactly as `app.kernel.interrogation` spells it. Three
    spellings of one path is how an assertion silently stops matching its measurement.
    """
    merged: dict[str, Any] = {}
    sidecar: dict[str, Any] = {}
    for prefix, payload in ((PROVIDER, provider), (CONSUMER, consumer), ("", boundary)):
        if payload is None:
            continue
        part = namespace(payload, prefix)
        for key, value in part.items():
            if key == PROVENANCE_KEY:
                sidecar.update(value)
                continue
            if key in merged:
                raise ContractError(
                    f"Two of the payloads given both report {key!r} at the top level. "
                    "Namespacing exists so this cannot happen silently — pass the "
                    "second one as provider= or consumer=."
                )
            merged[key] = value
    if sidecar:
        merged[PROVENANCE_KEY] = sidecar
    return merged


# -- checking ----------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """One claim on one interface that did not hold, with both parties on it.

    Covers `UNMEASURED` as well as `FAILED`, deliberately. An interface nobody could
    measure is not a satisfied interface, and the two recoveries differ — go and measure
    it, versus go and change the design — so the outcome is carried rather than
    flattened.
    """

    interface: str
    provider: str
    consumer: str
    result: AssertionResult
    note: str = ""

    @property
    def claim(self) -> str:
        return self.result.name

    @property
    def outcome(self) -> Outcome:
        return self.result.outcome

    @property
    def measurable(self) -> bool:
        return self.result.outcome is Outcome.FAILED

    def counterparty(self, party: str) -> str:
        """Who else this breaks, given the side that changed."""
        if party == self.provider:
            return self.consumer
        if party == self.consumer:
            return self.provider
        raise ContractError(
            f"{self.interface} is between {self.provider!r} and {self.consumer!r}; "
            f"{party!r} is neither."
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "interface": self.interface,
            "provider": self.provider,
            "consumer": self.consumer,
            **self.result.to_dict(),
        }
        if self.note:
            out["interface_note"] = self.note
        return out

    def __str__(self) -> str:
        if self.outcome is Outcome.UNMEASURED:
            head = (
                f"{self.interface}: NOT CHECKED between {self.provider} and "
                f"{self.consumer} — {self.result.reason}"
            )
        else:
            head = (
                f"{self.interface}: {self.provider} no longer meets {self.consumer} — "
                f"{self.result!s}"
            )
        return f"{head} ({self.note})" if self.note else head


@dataclass(frozen=True)
class ContractResult:
    """One interface, checked.

    Truthy only when every claim was measured *and* passed, inheriting
    `AssertionReport`'s reading of an unmeasured claim: the boundary has not been
    verified, so it is not satisfied.
    """

    interface: Interface
    report: AssertionReport

    @property
    def ok(self) -> bool:
        return self.report.ok

    def __bool__(self) -> bool:
        return self.ok

    @property
    def violations(self) -> tuple[Violation, ...]:
        """Every claim that failed or could not be measured, with both parties named."""
        return tuple(
            Violation(
                interface=self.interface.name,
                provider=self.interface.provider,
                consumer=self.interface.consumer,
                result=result,
                note=self.interface.note,
            )
            for result in self.report.results
            if result.outcome is not Outcome.PASSED
        )

    @property
    def failed(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.outcome is Outcome.FAILED)

    @property
    def unmeasured(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.outcome is Outcome.UNMEASURED)

    def summary(self) -> str:
        head = f"{self.interface.name} ({self.interface.provider} -> {self.interface.consumer}): "
        return head + self.report.summary()

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": self.interface.name,
            "provider": self.interface.provider,
            "consumer": self.interface.consumer,
            "ok": self.ok,
            "violations": [v.to_dict() for v in self.violations],
            **self.report.to_dict(),
        }


def check(
    interface: Interface,
    payload: Mapping[str, Any],
    *,
    parameters: Any | None = None,
) -> ContractResult:
    """Check one interface's claims against one measurement payload.

    `parameters` defaults to the interface's own resolved parameters, which is what
    makes `bound="=pivot_diameter_mm"` mean the contract's number rather than requiring
    every caller to resolve it. A caller with a design's parameters may pass those
    instead — a bound that reads a name neither side declares comes back `UNMEASURED`
    with the reason, which is `assertions`' existing behaviour and the right one.
    """
    resolved = parameters
    if resolved is None and len(interface.parameters):
        resolved = interface.parameters.resolve()
    return ContractResult(
        interface=interface,
        report=check_assertions(interface.claims, payload, parameters=resolved),
    )


def check_all(
    interfaces: Iterable[Interface],
    payloads: Mapping[str, Mapping[str, Any]],
) -> tuple[ContractResult, ...]:
    """Check several interfaces, each against the payload keyed by its name.

    An interface with no payload is **not skipped**. It is checked against an empty
    payload, so every one of its claims comes back `UNMEASURED` with a reason naming the
    paths that were not there. A contract check that quietly dropped the interfaces
    nobody measured would report a machine green on the boundaries nobody looked at,
    which is the same failure as a clash check that skipped pairs.
    """
    return tuple(check(i, payloads.get(i.name, {})) for i in interfaces)


# -- the compilable fragment (14.3) ------------------------------------------


def bind_into(interface: Interface, spec: DesignSpec) -> DesignSpec:
    """`spec` with the interface's parameters merged in — both sides' shared numbers.

    Returns a copy; specs are frozen. A parameter the spec already declares **identically**
    is left alone (the same declaration written twice is redundant, not a conflict). One
    declared *differently* is refused, and the message names the interface and both
    parties — 14.3's "a compile error at the interface naming both parties", which is
    the whole point: this is caught when the spec is bound, not when the two parts are
    put together three weeks later.

    Declaration order puts the interface's parameters **first**, because the spec's own
    parameters may derive from them (`arm_length_mm = "=pivot_centres_mm - 20"`) and a
    reader should see what came from outside before what was computed from it.
    `ParameterSet.resolve` sorts the dependency graph itself, so this is legibility
    rather than correctness.
    """
    existing = {p.name: p for p in spec.parameters}
    merged: list[Parameter] = []
    for shared in interface.parameters:
        mine = existing.get(shared.name)
        if mine is not None and not _same_declaration(mine, shared):
            raise ContractError(
                f"{spec.name} redeclares {shared.name!r}, which the interface "
                f"{interface.name!r} between {interface.provider!r} and "
                f"{interface.consumer!r} defines as {_declared_as(shared)}. This spec "
                f"has it as {_declared_as(mine)}. Both sides build from the contract's "
                "number or the two parts do not fit — delete the local declaration, or "
                "change the contract and rebuild both sides."
            )
        merged.append(shared)
    merged.extend(p for p in spec.parameters if p.name not in {s.name for s in interface.parameters})
    try:
        return spec.with_parameters(merged)
    except SpecError as exc:  # pragma: no cover - ParameterSet.of already refuses dupes
        raise ContractError(f"{interface.name} could not bind into {spec.name}: {exc}") from exc


def bind_both(
    interface: Interface, provider_spec: DesignSpec, consumer_spec: DesignSpec
) -> tuple[DesignSpec, DesignSpec]:
    """Both sides bound against one contract, in one call.

    The statement 14.3 actually makes — *the swingarm's spec imports the pivot contract,
    and so does the frame's* — written so it is one line and therefore actually done.
    Either side conflicting raises with both names on it.
    """
    return (bind_into(interface, provider_spec), bind_into(interface, consumer_spec))


def _same_declaration(left: Parameter, right: Parameter) -> bool:
    """Whether two declarations of one name say the same thing.

    Description is deliberately excluded: two authors wording the same number
    differently is not a conflict, and treating it as one would make the contract
    mechanism something people route around.
    """
    return (
        left.unit == right.unit
        and left.value == right.value
        and left.expression == right.expression
    )


def _declared_as(parameter: Parameter) -> str:
    if parameter.expression is not None:
        return f"{parameter.expression!r} ({parameter.unit.value or 'no unit'})"
    return f"{parameter.value} {parameter.unit.value or ''}".strip()


# -- change propagation (14.4, lifted to the product graph) ------------------


@dataclass(frozen=True)
class Impact:
    """One interface a change reaches, and who is on the other side of it.

    This is the answer 14.4 asks for at product level: `diff.py` says which features a
    parameter change reaches *within* one part; this says which *other components* now
    have to be re-checked, and names them.
    """

    interface: str
    changed: str
    role: str
    counterparty: str
    note: str = ""

    def __str__(self) -> str:
        line = (
            f"{self.interface}: {self.changed} is the {self.role}; "
            f"{self.counterparty} must be re-checked."
        )
        return f"{line} ({self.note})" if self.note else line

    def to_dict(self) -> dict[str, Any]:
        out = {
            "interface": self.interface,
            "changed": self.changed,
            "role": self.role,
            "counterparty": self.counterparty,
        }
        if self.note:
            out["note"] = self.note
        return out


def affected(
    interfaces: Iterable[Interface],
    changed: Iterable[str],
    *,
    structure: ProductStructure | None = None,
) -> tuple[Impact, ...]:
    """Which interfaces a set of changed components reaches, and the counterparty of each.

    With a `structure`, a party given as an occurrence path is resolved to the
    components along it, so changing a bracket buried three levels down still reaches a
    contract written against the sub-assembly that contains it. Without one, parties are
    matched by exact name — a narrower answer, and it is the honest one: guessing that
    `bike/rear.1/swingarm.1` mentions `swingarm` by substring would match `swingarm_pin`
    too, and a change-impact list with false entries stops being read.

    Order follows the interfaces as given, then the changed names as given, so two runs
    produce the same list and a diff of two impact reports is meaningful.
    """
    changed_names = tuple(dict.fromkeys(changed))
    impacts: list[Impact] = []
    for interface in interfaces:
        for role, party in ((PROVIDER, interface.provider), (CONSUMER, interface.consumer)):
            components = _party_components(party, structure)
            for name in changed_names:
                if name in components:
                    impacts.append(
                        Impact(
                            interface=interface.name,
                            changed=party,
                            role=role,
                            counterparty=interface.counterparty(party),
                            note=interface.note,
                        )
                    )
                    break
    return tuple(impacts)


def _party_components(party: str, structure: ProductStructure | None) -> frozenset[str]:
    """The component names a party string stands for.

    A bare component name stands for itself. An occurrence path stands for **every
    component on it**, root included: a contract written against `bike/rear.1` is about
    whatever that sub-assembly is made of, so a change to any of its contents is a
    change to what the contract describes. A path that does not resolve stands for
    itself and nothing else — an unresolvable path is a typo, and inventing components
    for it would put false entries in the impact list.
    """
    if structure is None:
        return frozenset({party})
    if party in structure:
        return frozenset({party})
    try:
        occurrence = structure.occurrence(party)
    except Exception:  # noqa: BLE001 - an unresolvable party is data, not a crash
        return frozenset({party})
    below = {
        other.component
        for other in structure.occurrences(leaves_only=False)
        if other.path == occurrence.path or other.path.startswith(occurrence.path + "/")
    }
    return frozenset({party, *occurrence.ancestry, *below})


__all__ = [
    "CONSUMER",
    "PROVIDER",
    "ContractResult",
    "Impact",
    "Interface",
    "Violation",
    "affected",
    "bind_both",
    "bind_into",
    "check",
    "check_all",
    "measurements",
    "namespace",
]
