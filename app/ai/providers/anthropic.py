"""Anthropic provider -- hosted Claude models.

Opt-in, never the default: this posts the geometry summary and load case to a
third party, which is the user's call to make rather than ours.

Uses the official `anthropic` SDK, imported lazily so an Ollama-only install
does not need the package at all.
"""

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.provider import (
    AssistantTurn,
    LLMError,
    LLMProvider,
    LLMRefusal,
    LLMUnavailable,
    ToolCall,
)
from app.ai.providers._json_schema import strictify

T = TypeVar("T", bound=BaseModel)


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
            block = {
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

    def __init__(
        self, api_key: str, model: str, timeout_seconds: float
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
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
        self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)
        return self._client

    def health(self) -> None:
        if not self._api_key:
            raise LLMUnavailable(
                "AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set."
            )
        self._get_client()

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> T:
        import anthropic

        client = self._get_client()
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                # Frozen prefix, marked for caching: the system prompt is
                # identical across every call at a given call site, and it is
                # by far the largest part of the request.
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                # `format` constrains generation to the schema; `effort` tunes
                # how much reasoning to spend. Both live under output_config.
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": strictify(schema.model_json_schema()),
                    },
                    "effort": effort,
                },
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailable("ANTHROPIC_API_KEY was rejected.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached. Retry shortly.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc

        # Check the stop reason before touching content: a refusal returns HTTP
        # 200 with an empty or partial content list, so indexing straight into
        # content[0] would raise IndexError instead of reporting the refusal.
        if response.stop_reason == "refusal":
            raise LLMRefusal(
                "The model declined this request. Rephrase the description, "
                "or switch to a local provider."
            )
        if response.stop_reason == "max_tokens":
            raise LLMError(
                "The model hit the output limit before finishing. Raise AI_MAX_TOKENS."
            )

        text = next((block.text for block in response.content if block.type == "text"), "")
        if not text.strip():
            raise LLMError("Anthropic returned an empty response.")

        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMError(
                f"Anthropic returned output that does not match the expected schema: {exc}"
            ) from exc

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        import anthropic

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

        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=anthropic_tools,
                messages=_to_anthropic_messages(messages),
            )
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailable("ANTHROPIC_API_KEY was rejected.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached. Retry shortly.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc

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
        return AssistantTurn(text="".join(text_parts), tool_calls=calls)
