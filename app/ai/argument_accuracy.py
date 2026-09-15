"""Selection accuracy and argument accuracy, measured together per turn -- master plan E22.4.

The tool-retrieval papers E16.1 rests on measure whether the right tool was *chosen*, and every
one of them scopes out whether it was called with the right *arguments*. In CAD that is the half
that matters: a wrong length is not an error, it is a plausible wrong part. So one turn here is
scored three ways, in order, each conditional on the one before:

1. **Offered** -- was the gold tool in what `tool_retrieval.select` showed the model? A tool the
   offer dropped is a retrieval failure, not a model failure, and the two need different fixes.
2. **Selected** -- did the model call the gold tool? Counted by name whether or not it was
   offered, because `ToolBox.call` accepts a tool the model was not shown.
3. **Arguments** -- given the right tool, did every gold argument arrive with the gold value?
   Numbers compare within the tolerance the *case* states (a case author says 0.0 when a length
   must be exact); strings, booleans and references compare exactly; lists and objects compare
   element by element under the same tolerance. A number sent as a string is wrong, because
   that is how a schema-violating call reaches a handler. Arguments the model added beyond the
   gold set are listed, not scored: an optional argument at its default is not an error, and
   deciding otherwise needs the tool's default, which the schema does not always state.

The rates are each over their own denominator and **None, never 0, when the denominator is
empty** -- argument accuracy over zero correct selections is not a measurement. The report names
the chooser and digests the case set, because a rate belongs to both.

**No number ships.** A rate needs a real model and a real case set; the chooser is injected, so a
scripted fixture proves the arithmetic here and `model_chooser` is the model. That run is THE
QUEUE D3.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.ai.provider import LLMProvider
from app.ai.tool_retrieval import DEFAULT_LIMIT, select


class AccuracyError(ValueError):
    """A case or run that cannot measure anything."""


class OfferedTool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Expected:
    """One gold argument value, and how far a number may be from it."""

    value: Any
    abs_tol: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.abs_tol) or self.abs_tol < 0.0:
            raise AccuracyError(f"A tolerance of {self.abs_tol} is not a tolerance.")


@dataclass(frozen=True)
class Call:
    """What the chooser did: one tool and its arguments."""

    tool: str
    arguments: Mapping[str, Any]


#: `(message, offered tools, context) -> the call made, or None for no call`.
Chooser = Callable[[str, Sequence[OfferedTool], str], Call | None]


@dataclass(frozen=True)
class TurnCase:
    name: str
    message: str
    gold_tool: str
    gold_arguments: Mapping[str, Expected]
    context: str = ""
    recent: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise AccuracyError(f"{self.name}: a turn needs the user's message.")
        if not self.gold_arguments:
            raise AccuracyError(
                f"{self.name}: a case with no gold arguments measures selection only; "
                "give at least one argument the call must carry."
            )

    def digest(self) -> str:
        payload = [
            self.name,
            self.message,
            self.gold_tool,
            {k: [v.value, v.abs_tol] for k, v in sorted(self.gold_arguments.items())},
            self.context,
            list(self.recent),
        ]
        return hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()


def matches(got: Any, expected: Any, abs_tol: float) -> bool:
    """Does `got` equal `expected`, numbers within `abs_tol`?"""
    if isinstance(expected, bool) or isinstance(got, bool):
        return type(got) is type(expected) and got == expected
    if isinstance(expected, (int, float)):
        return (
            isinstance(got, (int, float))
            and math.isfinite(float(got))
            and abs(float(got) - float(expected)) <= abs_tol
        )
    if isinstance(expected, Mapping):
        return (
            isinstance(got, Mapping)
            and set(got) == set(expected)
            and all(matches(got[k], expected[k], abs_tol) for k in expected)
        )
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(got, (list, tuple))
            and len(got) == len(expected)
            and all(matches(g, e, abs_tol) for g, e in zip(got, expected, strict=True))
        )
    return bool(got == expected)


@dataclass(frozen=True)
class TurnResult:
    case: str
    offered: bool
    offer_size: int
    called: str | None
    selected: bool
    #: Only meaningful when `selected`.
    right_arguments: tuple[str, ...] = ()
    wrong_arguments: tuple[str, ...] = ()
    missing_arguments: tuple[str, ...] = ()
    extra_arguments: tuple[str, ...] = ()

    @property
    def arguments_exact(self) -> bool:
        return self.selected and not self.wrong_arguments and not self.missing_arguments

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "offered": self.offered,
            "offer_size": self.offer_size,
            "called": self.called,
            "selected": self.selected,
            "arguments_exact": self.arguments_exact,
            "right_arguments": list(self.right_arguments),
            "wrong_arguments": list(self.wrong_arguments),
            "missing_arguments": list(self.missing_arguments),
            "extra_arguments": list(self.extra_arguments),
        }


def measure_turn(
    case: TurnCase,
    tools: Sequence[OfferedTool],
    chooser: Chooser,
    *,
    limit: int = DEFAULT_LIMIT,
) -> TurnResult:
    names = {tool.name for tool in tools}
    if case.gold_tool not in names:
        raise AccuracyError(
            f"{case.name}: {case.gold_tool} is not in the registry being measured, so no chooser "
            "could select it."
        )
    offer = select(tools, case.message, recent=case.recent, context=case.context, limit=limit).names()
    shown = [tool for tool in tools if tool.name in offer]
    call = chooser(case.message, shown, case.context)
    offered = case.gold_tool in offer
    if call is None:
        return TurnResult(case.name, offered, len(shown), None, False)
    if call.tool != case.gold_tool:
        return TurnResult(case.name, offered, len(shown), call.tool, False)
    right, wrong, missing = [], [], []
    for argument, expected in sorted(case.gold_arguments.items()):
        if argument not in call.arguments:
            missing.append(argument)
        elif matches(call.arguments[argument], expected.value, expected.abs_tol):
            right.append(argument)
        else:
            wrong.append(argument)
    extra = sorted(set(call.arguments) - set(case.gold_arguments))
    return TurnResult(
        case.name,
        offered,
        len(shown),
        call.tool,
        True,
        tuple(right),
        tuple(wrong),
        tuple(missing),
        tuple(extra),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


@dataclass(frozen=True)
class AccuracyReport:
    chooser: str
    case_set_digest: str
    limit: int
    results: tuple[TurnResult, ...] = field(default_factory=tuple)

    @property
    def offer_recall(self) -> float | None:
        """Turns whose gold tool was offered, over all turns."""
        return _rate(sum(r.offered for r in self.results), len(self.results))

    @property
    def selection_accuracy(self) -> float | None:
        """Turns that called the gold tool, over all turns."""
        return _rate(sum(r.selected for r in self.results), len(self.results))

    @property
    def selection_given_offered(self) -> float | None:
        """The model's share: gold called when it was on screen."""
        offered = [r for r in self.results if r.offered]
        return _rate(sum(r.selected for r in offered), len(offered))

    @property
    def argument_accuracy_given_tool(self) -> float | None:
        """Every gold argument right, over turns that called the gold tool."""
        selected = [r for r in self.results if r.selected]
        return _rate(sum(r.arguments_exact for r in selected), len(selected))

    @property
    def end_to_end(self) -> float | None:
        """Right tool and every gold argument right, over all turns."""
        return _rate(sum(r.arguments_exact for r in self.results), len(self.results))

    def to_dict(self) -> dict[str, Any]:
        return {
            "chooser": self.chooser,
            "case_set_digest": self.case_set_digest,
            "limit": self.limit,
            "turns": len(self.results),
            "offer_recall": self.offer_recall,
            "selection_accuracy": self.selection_accuracy,
            "selection_given_offered": self.selection_given_offered,
            "argument_accuracy_given_tool": self.argument_accuracy_given_tool,
            "end_to_end": self.end_to_end,
            "results": [r.to_dict() for r in self.results],
        }


def measure(
    cases: Sequence[TurnCase],
    tools: Sequence[OfferedTool],
    chooser: Chooser,
    *,
    chooser_name: str,
    limit: int = DEFAULT_LIMIT,
) -> AccuracyReport:
    if not chooser_name.strip():
        raise AccuracyError("Name the chooser: a model, its version, and how it was prompted.")
    if not cases:
        raise AccuracyError("There are no cases to measure.")
    names = [case.name for case in cases]
    if len(set(names)) != len(names):
        raise AccuracyError("Two cases share a name, so their results could not be told apart.")
    results = tuple(measure_turn(case, tools, chooser, limit=limit) for case in cases)
    digest = hashlib.sha256("".join(case.digest() for case in cases).encode()).hexdigest()
    return AccuracyReport(chooser_name, digest, limit, results)


CHOOSE_SYSTEM = (
    "You drive a CAD and analysis system through tools. Answer the user's request by calling "
    "the one tool that does it, with every argument the request determines."
)


def model_chooser(provider: LLMProvider, *, max_tokens: int) -> Chooser:
    """A `Chooser` backed by `provider.chat`: the first tool call of one turn, or None."""

    def choose(message: str, offered: Sequence[OfferedTool], context: str) -> Call | None:
        user = message if not context else f"Earlier in this conversation: {context}\n\n{message}"
        turn = provider.chat(
            system=CHOOSE_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in offered
            ],
            max_tokens=max_tokens,
        )
        if not turn.tool_calls:
            return None
        first = turn.tool_calls[0]
        return Call(first.name, dict(first.arguments))

    return choose


__all__ = [
    "AccuracyError",
    "AccuracyReport",
    "CHOOSE_SYSTEM",
    "Call",
    "Chooser",
    "Expected",
    "OfferedTool",
    "TurnCase",
    "TurnResult",
    "matches",
    "measure",
    "measure_turn",
    "model_chooser",
]
