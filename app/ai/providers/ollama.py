"""Ollama provider -- local inference, nothing leaves the machine.

This is the default for Kryova. CAD geometry and load cases are proprietary
engineering IP, so the shipping default must not post them to a third party.
It also means no API key, no per-token cost, and a desktop install that works
offline.

Ollama constrains generation to a JSON Schema via the `format` field, so the
structured-output contract in `provider.py` holds here the same as it does for
a hosted API.
"""

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.provider import AssistantTurn, LLMError, LLMProvider, LLMUnavailable, ToolCall

T = TypeVar("T", bound=BaseModel)

# Ollama has no effort parameter. Map the hint onto a token ceiling for the
# model's own reasoning so the knob still means something locally.
_EFFORT_PREDICT: dict[str, int] = {
    "low": 1_024,
    "medium": 4_096,
    "high": 8_192,
    "xhigh": 16_384,
    "max": 32_768,
}


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    def health(self) -> None:
        try:
            response = httpx.get(f"{self._base_url}/api/tags", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(
                f"Ollama is not reachable at {self._base_url}. Start it with "
                "`ollama serve`, or point KRYOVA at a different provider."
            ) from exc

        installed = {
            model.get("name", "").split(":")[0]
            for model in response.json().get("models", [])
        }
        if self._model.split(":")[0] not in installed:
            raise LLMUnavailable(
                f"Ollama is running but the model '{self._model}' is not installed. "
                f"Run `ollama pull {self._model}`."
            )

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> T:
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            # System first: the same ordering every hosted provider wants for
            # prefix caching, and Ollama reuses its own KV cache across calls
            # that share one.
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Constrains decoding to the schema rather than asking politely.
            "format": schema.model_json_schema(),
            "options": {
                "num_predict": max_tokens,
                "num_ctx": max(_EFFORT_PREDICT.get(effort, 4_096), 8_192),
            },
        }

        try:
            response = httpx.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama did not respond within {self._timeout:g}s. A larger model on "
                "CPU can exceed this -- raise AI_TIMEOUT_SECONDS or use a smaller model."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        content = response.json().get("message", {}).get("content", "")
        if not content.strip():
            raise LLMError("Ollama returned an empty response.")

        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            # Schema-constrained decoding makes this rare, but a small quantised
            # model can still stop early and truncate the JSON.
            raise LLMError(
                f"Ollama returned output that does not match the expected schema: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMError("Ollama returned malformed JSON.") from exc


    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        """Ollama speaks the OpenAI message and tool shape natively."""
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, *messages],
            "options": {"num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = tools

        try:
            response = httpx.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama did not respond within {self._timeout:g}s.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        message = response.json().get("message") or {}
        calls = []
        # Ollama omits call ids, so synthesise stable ones by position -- the
        # loop only needs them to pair a result back to its request.
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append(
                ToolCall(
                    id=raw.get("id") or f"call_{index}",
                    name=function.get("name", ""),
                    arguments=arguments or {},
                )
            )
        return AssistantTurn(text=message.get("content") or "", tool_calls=calls)
