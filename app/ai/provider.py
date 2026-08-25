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
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


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

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMError(RuntimeError):
    """The model could not be reached, or did not answer usably."""


class LLMUnavailable(LLMError):
    """The configured provider is not usable -- no key, or nothing listening."""


class LLMRefusal(LLMError):
    """The provider declined the request on safety grounds."""


class LLMProvider(ABC):
    """Turns a prompt into a validated instance of a Pydantic schema."""

    #: Shown to users when the provider is misconfigured, so the message can name it.
    name: str

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> T:
        """Return an instance of `schema`, or raise an `LLMError` subclass.

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

    @abstractmethod
    def health(self) -> None:
        """Raise `LLMUnavailable` if this provider could not serve a request."""
