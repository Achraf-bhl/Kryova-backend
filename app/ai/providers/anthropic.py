"""Anthropic provider -- hosted Claude models.

Opt-in, never the default: this posts the geometry summary and load case to a
third party, which is the user's call to make rather than ours.

Uses the official `anthropic` SDK, imported lazily so an Ollama-only install
does not need the package at all. Three details of the current Messages API
shape this file, and each one is a silent failure if got wrong:

**Structured output lives under `output_config.format`.** The older top-level
`output_format` parameter is deprecated API-wide, and the format block is
`{"type": "json_schema", "schema": ...}` with every object closed (see
`_json_schema.strictify`). `effort` is a sibling key of `format` in the same
`output_config` object -- not a top-level parameter.

**Sampling parameters are gone.** `temperature`, `top_p` and `top_k` are
rejected with a 400 on current Claude models, so nothing here sends them;
behaviour is steered by the prompt.

**`stop_reason` is checked before `content` is read.** A refusal returns HTTP
200 with an empty or partial content list, and a `max_tokens` stop returns a
fragment that looks like an answer. Indexing straight into `content[0]` turns
the first into an IndexError and the second into a truncated turn presented as
a finished one.
"""

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.provider import (
    AssistantTurn,
    Completion,
    LLMError,
    LLMProvider,
    LLMRefusal,
    LLMUnavailable,
    TokenUsage,
    ToolCall,
)
from app.ai.providers._json_schema import strictify

T = TypeVar("T", bound=BaseModel)

#: Effort levels the API accepts, cheapest first. A value outside this set is a
#: 400, so an unrecognised setting is clamped rather than shipped -- a typo in
#: `AI_EFFORT_INTERPRET` should not take the whole feature down.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "high"

#: The model Kryova targets when `AI_PROVIDER=anthropic` and nothing more
#: specific is configured. Bare alias, no date suffix: the aliases are complete
#: ids and appending a snapshot date 404s.
DEFAULT_MODEL = "claude-opus-5"

#: Models this provider has been exercised against. Not an allowlist -- a newer
#: id must work the day it ships -- but a configured model that matches none of
#: these prefixes is far more likely to be a copy-paste from the Ollama section
#: of the config than a real Claude release, and `health()` says so.
KNOWN_MODEL_PREFIXES = ("claude-",)


def _effort(value: str) -> str:
    """Clamp an effort hint onto the levels the API actually accepts."""
    normalised = (value or "").strip().lower()
    return normalised if normalised in EFFORT_LEVELS else DEFAULT_EFFORT


def _usage(response: Any) -> TokenUsage:
    """Read the usage block, folding cache tokens into the input count.

    Cache reads and cache writes are billed as input at different rates; the
    seam reports one input number, so they are summed here rather than leaking
    Anthropic's billing shape through `TokenUsage`.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0)
        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the OpenAI-shaped normal form into Anthropic content blocks.

    Two structural differences drive this: Anthropic carries tool calls as
    `tool_use` blocks inside the assistant turn rather than a sibling field,
    and tool results come back as `tool_result` blocks in a *user* turn rather
    than a `tool` role. Consecutive results must also be merged into one user
    message, which is why this cannot be a simple per-message map.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")

        if role == "tool":
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id", ""),
                "content": message.get("content") or "",
            }
            if message.get("is_error"):
                block["is_error"] = True
            # Merge into the preceding user turn if it is already tool results:
            # the API rejects a tool_use that is not answered in a single turn.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if role == "assistant":
            content: list[dict[str, Any]] = []
            if message.get("content"):
                content.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id", ""),
                        "name": function.get("name", ""),
                        "input": function.get("arguments") or {},
                    }
                )
            if content:
                out.append({"role": "assistant", "content": content})
            continue

        out.append({"role": "user", "content": message.get("content") or ""})
    return out


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self.model = (model or "").strip() or DEFAULT_MODEL
        self._timeout = timeout_seconds
        self._client: Any = None
        self._sdk: Any = None

    def _sdk_module(self) -> Any:
        """Import the SDK, or explain how to install it.

        Deliberately not a bare `import anthropic` at the top of each method:
        that raises `ModuleNotFoundError` before any of our error handling runs,
        so a missing optional dependency surfaced as a 500 rather than the 503
        with installation instructions that `LLMUnavailable` produces.
        """
        if self._sdk is not None:
            return self._sdk
        try:
            # Optional dependency: absent from requirements.txt on purpose, so
            # mypy cannot resolve it in a default install. `ignore_missing_imports`
            # only covers a package that is installed but unstubbed.
            import anthropic  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise LLMUnavailable(
                "AI_PROVIDER=anthropic needs the SDK: `pip install anthropic`. "
                "Or set AI_PROVIDER=ollama to run locally with no extra package."
            ) from exc
        self._sdk = anthropic
        return anthropic

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        anthropic = self._sdk_module()
        self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)
        return self._client

    def health(self) -> None:
        if not self._api_key:
            raise LLMUnavailable(
                "AI_PROVIDER=anthropic requires AI_API_KEY to be set to an Anthropic API key."
            )
        if not self.model.startswith(KNOWN_MODEL_PREFIXES):
            raise LLMUnavailable(
                f"AI_MODEL={self.model!r} is not an Anthropic model id. Set it to "
                f"a Claude alias such as {DEFAULT_MODEL!r} (no date suffix), or "
                "switch AI_PROVIDER to the backend that serves that model."
            )
        self._get_client()

    def _raise_for_sdk_error(self, exc: Exception) -> None:
        """Translate an SDK exception into this layer's vocabulary.

        Ordered most specific first: `APIStatusError` is the base of the status
        classes, so checking it before `AuthenticationError` would flatten a
        rejected key into a generic bad-gateway.
        """
        anthropic = self._sdk_module()
        if isinstance(exc, anthropic.AuthenticationError):
            raise LLMUnavailable("The Anthropic API key was rejected.") from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise LLMError("Anthropic rate limit reached. Retry shortly.") from exc
        if isinstance(exc, anthropic.APIConnectionError):
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc
        if isinstance(exc, anthropic.APIStatusError):
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        raise exc

    @staticmethod
    def _system_blocks(system: str) -> list[dict[str, Any]]:
        """The frozen prefix, marked for caching.

        The system prompt is identical across every call at a given call site
        and is by far the largest part of the request. Tools render before
        system, so one breakpoint on the last system block caches both.
        """
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> Completion[T]:
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=self._system_blocks(system),
                # `format` constrains generation to the schema; `effort` tunes
                # how much reasoning to spend. Both are keys of output_config.
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": strictify(schema.model_json_schema()),
                    },
                    "effort": _effort(effort),
                },
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001 - re-raised or translated below
            self._raise_for_sdk_error(exc)
            raise  # unreachable; _raise_for_sdk_error never returns

        # Check the stop reason before touching content: a refusal returns HTTP
        # 200 with an empty or partial content list, so indexing straight into
        # content[0] would raise IndexError instead of reporting the refusal.
        if response.stop_reason == "refusal":
            raise LLMRefusal(
                "The model declined this request. Rephrase the description, "
                "or switch to a local provider."
            )
        if response.stop_reason == "max_tokens":
            raise LLMError("The model hit the output limit before finishing. Raise AI_MAX_TOKENS.")

        text = next((block.text for block in response.content if block.type == "text"), "")
        if not text.strip():
            raise LLMError("Anthropic returned an empty response.")

        try:
            value = schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMError(
                f"Anthropic returned output that does not match the expected schema: {exc}"
            ) from exc
        return Completion(value=value, usage=_usage(response))

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        client = self._get_client()
        # Anthropic nests the schema differently: name/description/input_schema
        # at the top level of each tool, not under a "function" key.
        anthropic_tools = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"]["description"],
                "input_schema": tool["function"]["parameters"],
            }
            for tool in tools
        ]

        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": self._system_blocks(system),
            "messages": _to_anthropic_messages(messages),
        }
        # An empty `tools` list is not the same as no tools: the closing turn of
        # the agent loop withdraws them deliberately, and sending `tools: []`
        # is a validation error rather than "answer without tools".
        if anthropic_tools:
            request["tools"] = anthropic_tools

        try:
            response = client.messages.create(**request)
        except Exception as exc:  # noqa: BLE001 - re-raised or translated below
            self._raise_for_sdk_error(exc)
            raise  # unreachable; _raise_for_sdk_error never returns

        if response.stop_reason == "refusal":
            raise LLMRefusal("The model declined this request.")

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
                )

        # A `max_tokens` stop is not an error the way it is for structured
        # output -- there is usable text, and in an agent loop discarding the
        # turn wastes everything it just did. Surface it instead, so the caller
        # can tell the user the answer was cut off rather than presenting a
        # sentence that stops mid-word as a finished thought.
        truncated = response.stop_reason == "max_tokens"
        return AssistantTurn(
            text="".join(text_parts),
            tool_calls=calls,
            usage=_usage(response),
            truncated=truncated,
        )
