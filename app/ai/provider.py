"""The LLM seam.

The fourth interface in this codebase, alongside Solver, JobQueue and MediaStore:
everything above it speaks `LLMProvider`, so swapping a local Ollama model for a
hosted API is a config change, not a rewrite. Never reach around it -- callers
must not import a vendor SDK directly.

Every provider takes the same three things (a frozen system prompt, a user
message, a Pydantic schema) and returns a validated instance of that schema.
Structured output is not optional: an engineering tool cannot parse prose and
hope, so a provider that cannot constrain generation to a schema does not belong
here.

Both entry points also return a `TokenUsage`. Reporting it is part of the
interface rather than an optional extra: tokens are money, and a provider that
quietly drops the `usage` block makes spend unmeterable for everything above it.
A provider whose backend reports nothing returns zeros, which is honest -- the
caller can tell "free" from "unknown" by asking which provider answered.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class TokenUsage:
    """What one model call cost, in tokens.

    Named after the two things every provider reports under some spelling --
    Anthropic's `input_tokens`/`output_tokens`, OpenAI's
    `prompt_tokens`/`completion_tokens`, Ollama's `prompt_eval_count`/`eval_count`.
    Cached-read and cache-write tokens are folded into `prompt_tokens`: they are
    billed as input, and splitting them here would push provider billing detail
    through a seam whose whole point is that callers do not know who answered.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True)
class Completion(Generic[T]):
    """A validated schema instance and what it cost to produce.

    `complete()` returns this rather than the bare model so the caller can meter
    the call. Unwrapping is `.value`; everything that only wants the answer says
    so at one obvious place instead of the usage silently disappearing.
    """

    value: T
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass
class ToolCall:
    """One tool invocation the model asked for."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    """What the model produced in one step of the agent loop.

    `tool_calls` empty means the model is done and `text` is its answer.
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    #: True when the provider stopped because it hit the output limit. The text
    #: is then a fragment, not an answer, and the caller must say so rather than
    #: presenting a sentence that stops mid-word as a considered reply.
    truncated: bool = False

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMError(RuntimeError):
    """The model could not be reached, or did not answer usably."""


class LLMUnavailable(LLMError):
    """The configured provider is not usable -- no key, or nothing listening."""


class LLMRefusal(LLMError):
    """The provider declined the request on safety grounds."""


class VisionUnsupported(LLMUnavailable):
    """This provider, or the model it is configured with, cannot look at an image.

    Its own error rather than a bare `LLMUnavailable` because the caller does
    something different with it. "Ollama is not running" is a fault to report;
    "this model has no eyes" is a *capability* answer, and Phase 4.2's whole
    discipline is that it must come back as `unchecked` rather than as a pass or
    a failure. Folding the two together would make a missing vision model look
    like a broken install.
    """


class LLMProvider(ABC):
    """Turns a prompt into a validated instance of a Pydantic schema."""

    #: Shown to users when the provider is misconfigured, so the message can name it.
    name: str

    #: The model this provider was built for, for the usage ledger. Providers
    #: set it in `__init__`; the seam only needs it to be a string.
    model: str = "unknown"

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> Completion[T]:
        """Return an instance of `schema` plus its usage, or raise an `LLMError`.

        `system` is frozen per call site and placed first so providers that
        support prompt caching get a stable prefix. `effort` is a hint --
        providers that have no equivalent ignore it rather than failing.
        """

    @abstractmethod
    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        """One step of an agent loop: given the transcript, decide what to do next.

        `messages` uses the OpenAI-shaped normal form, which Ollama and every
        OpenAI-compatible server take as-is and the Anthropic provider
        translates:

            {"role": "user",      "content": str}
            {"role": "assistant", "content": str, "tool_calls": [...]}
            {"role": "tool",      "tool_call_id": str, "name": str, "content": str}

        `tools` is a list of OpenAI function schemas. Providers must return an
        `AssistantTurn`; a provider that cannot do tool calling raises
        `LLMUnavailable` rather than silently answering without them.
        """

    def look(
        self,
        *,
        system: str,
        user: str,
        images: Sequence[bytes],
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> Completion[T]:
        """Same contract as `complete`, with pictures attached. PNG bytes, in order.

        **Not abstract, and that is the decision.** Sight is a capability some
        providers and most models do not have, so an abstract method would force
        every provider to grow a stub and would say nothing about whether a call
        will work. The default refuses by name; a provider that can see
        overrides it.

        The images are unlabelled on the wire — Ollama attaches them to the
        message and has nowhere to put a caption — so the *order* is the only
        thing tying an image to what it is a picture of, and the caller names
        that order in `user`. Providers must not reorder them.

        Raises `VisionUnsupported` when the model cannot see, which is a
        different answer from failing: see Phase 4.2, where a check that could
        not run is `unchecked` and never a pass.
        """
        raise VisionUnsupported(
            f"The {self.name} provider cannot show an image to a model, so the "
            "visual check cannot run. Configure a provider and model that can "
            "see, or rely on the measured checks alone."
        )

    @abstractmethod
    def health(self) -> None:
        """Raise `LLMUnavailable` if this provider could not serve a request."""
