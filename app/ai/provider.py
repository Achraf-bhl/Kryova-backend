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
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


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
    def health(self) -> None:
        """Raise `LLMUnavailable` if this provider could not serve a request."""
