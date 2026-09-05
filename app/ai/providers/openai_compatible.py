"""Any server that speaks the OpenAI chat-completions shape.

One implementation covers OpenAI itself, LM Studio, vLLM, llama.cpp's server,
Groq, Together, OpenRouter and most self-hosted gateways -- they all expose
`POST /v1/chat/completions` and accept `response_format: {"type": "json_schema"}`.
Point `AI_BASE_URL` at whichever one you run.

Kept deliberately SDK-free: the wire format is small and stable, and adding the
`openai` package to reach a local llama.cpp would be a dependency for nothing.
"""

import base64
import json
from collections.abc import Sequence
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


class _ResponseFormatUnsupported(Exception):
    """The endpoint refused `response_format`, not the request itself."""


#: Substrings an endpoint uses to say it does not do schema-constrained output.
#: DeepSeek answers 400 "This response_format type is unavailable now"; others
#: name the field. Matched on the body because none of them use a distinct code.
_RESPONSE_FORMAT_REJECTIONS = (
    "response_format",
    "json_schema",
    "response format",
)


def _unfence(content: str) -> str:
    """Strip a ```json fence, which a model adds when only asked for JSON.

    `json_schema` mode returns bare JSON; the `json_object` fallback is a
    prompt instruction, and a model following one of those wraps its answer in
    a code fence often enough that not handling it would turn the fallback into
    a schema-validation error.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body[:4].lower().startswith("json"):
        body = body[4:]
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        vision_model: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.model = model
        #: Which model a visual check runs against, when it is not the one doing
        #: the engineering. Unlike Ollama there is nothing to probe here — this
        #: endpoint family publishes no capability list — so a model that cannot
        #: see answers with a 400, which `look` reports rather than swallowing.
        self._vision_model = (vision_model or "").strip() or None
        self._timeout = timeout_seconds
        # Learned on first use, then remembered: see `_structured_payload`.
        self._json_schema_supported = True

    def _extra_body(self) -> dict[str, Any]:
        """Vendor fields to merge into every request. Empty for a plain endpoint.

        A subclass overrides this to reach a feature its vendor exposes outside
        the OpenAI schema -- NVIDIA's reasoning controls are the case it exists
        for. It is a hook rather than a config knob because the fields are not
        interchangeable: this endpoint family *rejects* what it does not know.
        NVIDIA answers `400 Validation: Unsupported parameter(s)`, so a field
        that is right for one server takes another one down entirely, and
        "merge whatever the operator put in the env" would be a way to break
        every call with a typo.
        """
        return {}

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

    def _structured_payload(
        self, system: str, user: str, schema: type[T], max_tokens: int
    ) -> dict[str, Any]:
        """One structured-output request, in whichever dialect this server takes.

        `json_schema` is the one that actually constrains decoding, so it is
        tried first and kept whenever it works. Not every OpenAI-compatible
        endpoint has it: DeepSeek answers 400 "This response_format type is
        unavailable now" for both its models, and it is the endpoint Kryova
        runs against, so a hard failure here would take out load-case parsing
        entirely. The fallback asks for `json_object` -- which does guarantee
        syntactically valid JSON -- and puts the schema in the system message.
        Pydantic still validates the result either way, so the guarantee that
        matters (nothing malformed reaches the caller) is unchanged; what is
        lost is the server refusing to emit a wrong shape in the first place.
        """
        json_schema = strictify(schema.model_json_schema())
        payload: dict[str, Any] = {
            **self._extra_body(),
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._json_schema_supported:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": json_schema,
                },
            }
            return payload

        payload["response_format"] = {"type": "json_object"}
        payload["messages"][0]["content"] = (
            f"{system}\n\nAnswer with a single JSON object and nothing else -- no "
            f"prose, no code fence. It must validate against this JSON Schema:\n"
            f"{json.dumps(json_schema)}"
        )
        return payload

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            status = exc.response.status_code
            if status in (401, 403):
                raise LLMUnavailable("The API key was rejected.") from exc
            body = exc.response.text
            if (
                status == 400
                and "response_format" in payload
                and any(marker in body.lower() for marker in _RESPONSE_FORMAT_REJECTIONS)
            ):
                raise _ResponseFormatUnsupported(body[:200]) from exc
            raise LLMError(f"Chat completion failed ({status}): {body[:200]}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Chat completion request failed: {exc}") from exc

        return dict(response.json())

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> Completion[T]:
        try:
            body = self._post(self._structured_payload(system, user, schema, max_tokens))
        except _ResponseFormatUnsupported:
            # The endpoint speaks the chat API but not schema-constrained output.
            # Remembered, so this costs one 400 per process and not one per call.
            self._json_schema_supported = False
            body = self._post(self._structured_payload(system, user, schema, max_tokens))
        choices = body.get("choices") or []
        if not choices:
            raise LLMError("The model returned no choices.")

        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            raise LLMRefusal("The provider's content filter rejected this request.")
        if choice.get("finish_reason") == "length":
            raise LLMError("The model hit the output limit before finishing. Raise AI_MAX_TOKENS.")

        content = _unfence((choice.get("message") or {}).get("content") or "")
        if not content.strip():
            raise LLMError("The model returned an empty response.")

        try:
            return Completion(value=schema.model_validate_json(content), usage=_usage(body))
        except ValidationError as exc:
            raise LLMError(
                f"The model returned output that does not match the expected schema: {exc}"
            ) from exc

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
        """`complete`, with the renders as `image_url` parts carrying data URIs.

        A data URI rather than a link, and that is not merely convenient: the
        renders exist in memory and have no URL, and giving one would mean this
        service publishing an engineering drawing at a fetchable address for a
        third party to pull. The bytes go in the request and nowhere else.
        """
        if not images:
            raise LLMError("A visual check needs at least one image.")
        payload = self._structured_payload(system, user, schema, max_tokens)
        payload["model"] = self._vision_model or self._model
        payload["messages"][-1]["content"] = [
            *(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(one).decode("ascii")
                    },
                }
                for one in images
            ),
            # The question after the pictures: the parts are read in order, and
            # one asked first is asked about nothing.
            {"type": "text", "text": payload["messages"][-1]["content"]},
        ]

        try:
            body = self._post(payload)
        except _ResponseFormatUnsupported:
            self._json_schema_supported = False
            retry = self._structured_payload(system, user, schema, max_tokens)
            retry["model"] = payload["model"]
            retry["messages"][-1]["content"] = payload["messages"][-1]["content"]
            body = self._post(retry)

        choices = body.get("choices") or []
        if not choices:
            raise LLMError("The model returned no choices.")
        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            raise LLMRefusal("The provider's content filter rejected this render.")
        if choice.get("finish_reason") == "length":
            raise LLMError("The model hit the output limit before finishing. Raise AI_MAX_TOKENS.")

        content = _unfence((choice.get("message") or {}).get("content") or "")
        if not content.strip():
            raise LLMError("The model returned an empty response.")
        try:
            return Completion(value=schema.model_validate_json(content), usage=_usage(body))
        except ValidationError as exc:
            raise LLMError(
                f"The model returned output that does not match the expected schema: {exc}"
            ) from exc

    def _chat_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            **self._extra_body(),
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
        return payload

    def _parse_turn(self, body: dict[str, Any]) -> AssistantTurn:
        """Read one chat response into the agent's normal form.

        Its own method so a vendor subclass can reinterpret the response without
        also owning the request. NVIDIA needs exactly that: it returns the
        model's reasoning in a second field, and what belongs in `text` depends
        on it.
        """
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

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        return self._parse_turn(self._post(self._chat_payload(system, messages, tools, max_tokens)))
