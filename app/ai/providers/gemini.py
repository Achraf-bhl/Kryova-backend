"""Google Gemini, reached through its OpenAI-compatible endpoint.

Gemini publishes `POST /v1beta/openai/chat/completions` in the OpenAI shape, so almost
none of this is new code: `OpenAICompatibleProvider` already speaks that dialect, handles
the `json_schema` negotiation and reads the same usage block. What this subclass adds is
**one field**, for one measured reason.

## Thinking is on by default, and on a small call it is most of the bill

Measured 2026-09-22 against `gemini-2.5-flash`, on the smallest decision this codebase
makes -- *"is a 12 mm plate thicker than 5 mm"*, answered into a two-value enum:

    default                15 prompt + 7 completion, total 81
    reasoning_effort=none  15 prompt + 6 completion, total 21

The prompt and the answer are 22 tokens of that 81. The other 59 are invisible reasoning
about whether 12 exceeds 5, billed and waited for. At `MAX_TOKENS = 24` -- which is
generous for a one-word answer -- those hidden tokens exhaust the budget before the answer
is emitted, and the call comes back `finish_reason: length`. That is not a hypothetical:
it is what the first live run of `app/ai/decide.py` did, three times out of three, and the
symptom ("the model hit the output limit") points at the token cap rather than at the
cause.

## Why it is a returned value rather than a flag

`_plan_reasoning` answers per call, and the answer is the request's own: a structured
call (or a chat call with no tools) asks for `reasoning_effort=none`, an agent step asks
for nothing and gets the vendor's default. It used to be a flag toggled on the provider
for the duration of a structured call, which was a race -- the provider is one cached
instance shared by every request thread, so a concurrent agent step could be sent
`reasoning_effort=none`, silently un-thinking, with no error to show for it.

**And structured is the right boundary, not "always off".** A `complete()` call has a
closed output shape -- a decision, a parsed load case, a visual finding -- and reasoning
tokens cannot improve a value that a schema already constrains to one of N. A `chat()`
call with tools is the agent building geometry over tens of interdependent steps, which
is exactly where reasoning earns its cost.

## What this provider deliberately does not claim

**No logprobs, and therefore no measured confidence.** Checked both ways that day: the
OpenAI-compatible endpoint rejects `logprobs` and `top_logprobs` with *"Unknown name …
Cannot find field"*, and the native endpoint answers *"Logprobs is not enabled for
models/gemini-2.5-flash"*. No model reachable with the account's key returned one. That is
why `app/ai/decide.py` carries a `Confidence.basis` rather than a float -- a provider that
returns logprobs can report a measured probability and here it must say it has none.
"""

from __future__ import annotations

from typing import Any

from app.ai.providers.openai_compatible import OpenAICompatibleProvider, Reasoning

#: Gemini's OpenAI-compatible base. The provider appends `/chat/completions`.
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

#: The value Gemini takes for "do not think". Sent explicitly rather than omitted:
#: the model reasons by default, so leaving the field out is not the same as turning
#: it off.
NO_REASONING = "none"


class GeminiProvider(OpenAICompatibleProvider):
    """Gemini over the OpenAI dialect, with reasoning disabled on structured calls."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        base_url: str | None = None,
        vision_model: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url or DEFAULT_BASE_URL,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            vision_model=vision_model,
        )

    def _plan_reasoning(
        self, *, effort: str | None, messages: list[dict[str, Any]] | None
    ) -> Reasoning:
        """`reasoning_effort: none` for a structured or tool-less call, else nothing.

        Only emitted when reasoning is being turned *off*. Sending `reasoning_effort`
        on an ordinary agent step would pin the model to a default this code has not
        measured, and the honest default is the vendor's own.
        """
        if effort is None:
            return Reasoning()
        return Reasoning(fields={"reasoning_effort": NO_REASONING})


__all__ = ["DEFAULT_BASE_URL", "NO_REASONING", "GeminiProvider"]
