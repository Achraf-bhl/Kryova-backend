"""Any server that speaks the OpenAI chat-completions shape.

One implementation covers OpenAI itself, LM Studio, vLLM, llama.cpp's server,
Groq, Together, OpenRouter and most self-hosted gateways -- they all expose
`POST /v1/chat/completions` and accept `response_format: {"type": "json_schema"}`.
Point `AI_BASE_URL` at whichever one you run.

Kept deliberately SDK-free: the wire format is small and stable, and adding the
`openai` package to reach a local llama.cpp would be a dependency for nothing.
"""

import json
from typing import Any, TypeVar

import httpx
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


def _usage(body: dict[str, Any]) -> TokenUsage:
    """Read the `usage` block, tolerating a server that omits it.

    Several OpenAI-compatible servers (older llama.cpp builds, some gateways)
    answer without one. Zeros are the honest report there; inventing an
    estimate would put fiction into the spend ledger.
    """
    usage = body.get("usage") or {}
    return TokenUsage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )


def _to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the agent's normal form into the OpenAI message shape.

    Two things differ and both are silent failures if missed:

    * ``tool_calls[].function.arguments`` must be a **JSON-encoded string**, not
      an object. The agent stores the parsed dict (that is what every other
      provider wants), so replaying a transcript verbatim sends an object and a
      strict endpoint answers 400 with no indication of which field was wrong.
    * Tool messages carry ``is_error``, which is ours, not OpenAI's. Only the
      documented keys go on the wire; the error text is already in ``content``.
    """
    wire: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool":
            wire.append(
                {
                    key: message[key]
                    for key in ("role", "tool_call_id", "name", "content")
                    if key in message
                }
            )
            continue

        calls = message.get("tool_calls")
        if not calls:
            wire.append(message)
            continue

        normalised = []
        for call in calls:
            function = dict(call.get("function") or {})
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                function["arguments"] = json.dumps(arguments or {})
            normalised.append({**call, "function": function})
        wire.append({**message, "tool_calls": normalised})
    return wire


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self, base_url: str, api_key: str | None, model: str, timeout_seconds: float
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.model = model
        self._timeout = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        # Local servers (LM Studio, llama.cpp, vLLM) usually need no key.
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def health(self) -> None:
        try:
            response = httpx.get(f"{self._base_url}/models", headers=self._headers(), timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(
                f"No OpenAI-compatible server answered at {self._base_url}."
            ) from exc

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> Completion[T]:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": strictify(schema.model_json_schema()),
                },
            },
        }

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(f"The model did not respond within {self._timeout:g}s.") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise LLMUnavailable("The API key was rejected.") from exc
            raise LLMError(
                f"Chat completion failed ({exc.response.status_code}): {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Chat completion request failed: {exc}") from exc

        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise LLMError("The model returned no choices.")

        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            raise LLMRefusal("The provider's content filter rejected this request.")
        if choice.get("finish_reason") == "length":
            raise LLMError("The model hit the output limit before finishing. Raise AI_MAX_TOKENS.")

        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            raise LLMError("The model returned an empty response.")

        try:
            return Completion(value=schema.model_validate_json(content), usage=_usage(body))
        except ValidationError as exc:
            raise LLMError(
                f"The model returned output that does not match the expected schema: {exc}"
            ) from exc

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                *_to_wire(messages),
            ],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(f"The model did not respond within {self._timeout:g}s.") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise LLMUnavailable("The API key was rejected.") from exc
            raise LLMError(
                f"Chat failed ({exc.response.status_code}): {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Chat request failed: {exc}") from exc

        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise LLMError("The model returned no choices.")
        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            raise LLMRefusal("The provider's content filter rejected this request.")

        message = choice.get("message") or {}
        calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append(
                ToolCall(
                    id=raw.get("id") or function.get("name", "call"),
                    name=function.get("name", ""),
                    arguments=arguments or {},
                )
            )
        return AssistantTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            usage=_usage(body),
            truncated=choice.get("finish_reason") == "length",
        )
