"""Any server that speaks the OpenAI chat-completions shape.

One implementation covers OpenAI itself, vLLM, Groq, Together, OpenRouter and
most gateways -- they all expose `POST /v1/chat/completions` and accept
`response_format: {"type": "json_schema"}`. Point `AI_BASE_URL` at whichever one
you run. A vendor whose dialect differs (DeepSeek, NVIDIA) subclasses this and
overrides the small class attributes and the one hook below; nothing else in the
product knows which vendor answered.

Kept deliberately SDK-free: the wire format is small and stable, and adding the
`openai` package would be a dependency for nothing.

**What a vendor subclass can change, and nothing more:**

* `_max_tokens_field` -- the request field that caps output. OpenAI's reasoning
  models want `max_completion_tokens`; DeepSeek documents `max_tokens` and
  silently ignores the other, so a cap the operator set would not apply.
* `_reasoning_field` -- the message field a vendor requires its own reasoning
  echoed back in (DeepSeek's `reasoning_content`). `None` for everyone else, and
  then reasoning is neither kept nor replayed.
* `_plan_reasoning` -- the extra request fields that switch reasoning on or off
  for one call, and how many tokens of headroom it needs. It returns a value
  instead of setting state because the provider is one cached instance shared by
  every request thread: a flag toggled around a call is a race.
* `_STREAMING` / `_STREAM_USAGE` -- whether the vendor is known to stream
  correctly, and to take `stream_options`. Unknown fields are rejected outright
  by some servers, so an option is sent only where it is known to be taken.
"""

import base64
import json
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.provider import (
    AssistantTurn,
    ChatEvent,
    Completion,
    Finished,
    LLMBusy,
    LLMError,
    LLMProvider,
    LLMRefusal,
    LLMUnavailable,
    TextDelta,
    TokenUsage,
    ToolCall,
    image_media_type,
)
from app.ai.providers._json_schema import strictify

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Tokens a reasoning model may spend thinking on one call, beyond the answer's
#: own cap -- the default of `AI_REASONING_BUDGET`. NVIDIA takes it as a field
#: that bounds the reasoning; DeepSeek counts reasoning against the output cap
#: and gets it as headroom. Generous enough for a real CATIA decision, short of
#: a runaway: a model will happily spend thousands of tokens deliberating over
#: "give the answer blue" if nothing stops it.
DEFAULT_REASONING_BUDGET: Final = 8_192

#: Seconds to wait before each retry, so the request is tried `len + 1` times.
#: Sized from a live measurement, 2026-09-23, against Gemini's free tier: a
#: tool-calling turn carrying a real ~46-tool offer 503'd five times running
#: ("This model is currently experiencing high demand") while no-tool turns on
#: the same account answered every time. The bursts lasted several seconds to
#: low tens of seconds -- past what a sub-2 s budget rides out -- and the same
#: request tried a little later routinely succeeds, so this is capacity
#: backpressure and not a rejection. Every call here is idempotent (a chat
#: completion mutates nothing; a repeat is a fresh sample, not a duplicated
#: action), and a timeout is *not* retried: the caller's patience is spent.
RETRY_BACKOFF_S: Final[tuple[float, ...]] = (1.0, 3.0, 8.0)
HTTP_ATTEMPTS = len(RETRY_BACKOFF_S) + 1

#: Statuses worth another attempt. A 4xx other than 429 is a bug in what was
#: sent and fails identically forever.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: The retryable statuses that mean "not now" rather than "something broke". When the transport
#: has retried them to the end the failure is `LLMBusy`, not a bare `LLMError`, so the agent
#: can end the turn with a Continue instead of an error (ROAD_TO_10 3.6). **500 is not here on
#: purpose**: an internal error is a fault in the service, retrying it is a courtesy, and
#: telling the user "it was only busy" about one would be a guess.
BUSY_STATUSES = frozenset({429, 502, 503, 504})

#: Ceiling on how long a `Retry-After` header can make one attempt wait.
MAX_RETRY_WAIT_S = 8.0

#: Attempts at one structured answer. The second is told what was wrong with the
#: first; a third would draw from the same distribution as the second.
STRUCTURED_ATTEMPTS = 2

#: Finish reasons that mean the server stopped generating for its own reasons
#: (DeepSeek documents both). The partial answer is not an answer and, unlike
#: `length`, asking again can succeed.
INTERRUPTED_FINISH_REASONS = frozenset({"insufficient_system_resource", "aborted"})


def _carrying(exc: LLMError, spent: TokenUsage) -> None:
    """Add the usage of attempts already billed to `exc`'s own, in place.

    Mutates the exception rather than wrapping it, so its type (a `LLMUnavailable`
    for a rejected key stays one) and message are untouched; only the accounting
    moves. The caller re-raises the same object.
    """
    exc.usage = spent + exc.usage


def _cached_tokens(usage: dict[str, Any]) -> int:
    """How many prompt tokens the server says it read from its prompt cache.

    DeepSeek spells it `prompt_cache_hit_tokens` at the top of the block; OpenAI
    (and the vendors that copy it) nest it as `prompt_tokens_details.cached_tokens`.
    A server that reports neither gets 0, which is the honest answer and not a
    claim that nothing was cached -- the ledger cannot tell the two apart, and
    pricing a miss as a miss is the safe direction to be wrong in.
    """
    hit = usage.get("prompt_cache_hit_tokens")
    if isinstance(hit, int) and not isinstance(hit, bool):
        return max(0, hit)
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
        if isinstance(cached, int) and not isinstance(cached, bool):
            return max(0, cached)
    return 0


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
        cached_prompt_tokens=_cached_tokens(usage),
    )


def _log_prompt_cache(usage: dict[str, Any] | None) -> None:
    """Say how much of the prompt the server billed as already seen, when it says.

    DeepSeek reports `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`. It is
    the only direct measure that the prompt is byte-stable between steps
    (`tests/test_prompt_cache_stability.py` is the offline half), and a vendor
    that reports nothing is simply silent here.
    """
    if not usage:
        return
    hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
    if isinstance(hit, int) and isinstance(miss, int) and hit + miss > 0:
        logger.info("prompt cache: %d of %d prompt tokens were hits", hit, hit + miss)


def _retry_after(response: httpx.Response | None) -> float | None:
    """The provider's own `Retry-After` in seconds, or None when it sent none we can read.

    Only the delta-seconds form: the HTTP-date form is legal but no provider this module
    talks to sends it, and parsing a date to turn it into a wait is where a clock skew would
    become a one-hour sleep.
    """
    if response is None:
        return None
    try:
        asked = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return asked if asked >= 0 else None


def _describe_busy(status: int) -> str:
    return {
        429: "HTTP 429, rate limited",
        502: "HTTP 502, bad gateway",
        503: "HTTP 503, overloaded or unavailable",
        504: "HTTP 504, gateway timeout",
    }.get(status, f"HTTP {status}")


def _finish_reason(body: dict[str, Any]) -> str | None:
    choices = body.get("choices") or [{}]
    reason = choices[0].get("finish_reason")
    return str(reason) if reason else None


@dataclass(frozen=True)
class Reasoning:
    """What one call asks of the model's reasoning, in a vendor's own terms.

    `enabled` is whether the model will reason on this call -- the provider needs
    it to decide whether an earlier turn's reasoning must travel with the
    transcript. `fields` are merged into the request body. `extra_tokens` is the
    headroom added to the output cap, because a vendor that counts reasoning
    against it would otherwise cut the answer off after thinking.
    """

    enabled: bool = False
    fields: dict[str, Any] = field(default_factory=dict)
    extra_tokens: int = 0


def _to_wire(
    messages: list[dict[str, Any]], *, reasoning_field: str | None = None
) -> list[dict[str, Any]]:
    """Translate the agent's normal form into the OpenAI message shape.

    Three things differ and each is a silent failure if missed:

    * ``tool_calls[].function.arguments`` must be a **JSON-encoded string**, not
      an object. The agent stores the parsed dict (that is what every other
      provider wants), so replaying a transcript verbatim sends an object and a
      strict endpoint answers 400 with no indication of which field was wrong.
    * Only the documented keys go on the wire. Tool messages carry ``is_error``
      and ``name``, assistant messages carry ``reasoning`` -- all ours, and a
      strict endpoint rejects a key it does not know. The error text is already
      in ``content`` and ``tool_call_id`` already ties a result to its call.
    * ``reasoning`` is sent, as ``reasoning_field``, only when the caller names
      one -- that is, only to a vendor that requires it and only on a call where
      the model will reason.
    """
    wire: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            wire.append(
                {
                    key: message[key]
                    for key in ("role", "tool_call_id", "content")
                    if key in message
                }
            )
            continue

        if role != "assistant":
            wire.append(message)
            continue

        entry: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
        calls = message.get("tool_calls")
        if calls:
            normalised = []
            for call in calls:
                function = dict(call.get("function") or {})
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    function["arguments"] = json.dumps(arguments or {})
                normalised.append({**call, "function": function})
            entry["tool_calls"] = normalised
        if reasoning_field and message.get("reasoning") is not None:
            entry[reasoning_field] = message["reasoning"]
        wire.append(entry)
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


_NO_STRUCTURED_OUTPUT = (
    "This endpoint accepts neither json_schema nor json_object output, so a structured "
    "answer cannot be requested from it."
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


def _repair_message(problem: str) -> str:
    """The second attempt's brief: what was wrong, and the one rule that fixes it."""
    first_line = problem.splitlines()[0] if problem else "The answer was empty."
    return (
        f"Your previous answer could not be used: {first_line} Answer again with "
        "only the JSON object, every required field present, no field left as a "
        "placeholder, and nothing outside the object."
    )


class _StreamAssembler:
    """Folds a server-sent-events chat stream back into one response body.

    The stream carries the answer in pieces: text in `delta.content`, a
    reasoning vendor's chain of thought in `delta.reasoning_content`, and each
    tool call as fragments keyed by `index` -- the first names the call, the
    rest append to its `arguments` string. `body()` rebuilds the shape a
    non-streaming response has, so `_parse_turn` reads both identically and a
    vendor's override of it applies to both.
    """

    def __init__(self) -> None:
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None

    @property
    def finished(self) -> bool:
        """Whether the server said why it stopped -- the mark of a whole stream."""
        return self.finish_reason is not None

    def feed(self, line: str) -> str:
        """Take one line of the stream; return the text it carried, if any.

        Blank lines, `:` comments (keep-alives) and non-`data:` fields are not
        events. One unreadable `data:` line is skipped rather than fatal: an
        answer that is otherwise arriving fine is not worth discarding for it.
        """
        line = line.strip()
        if not line.startswith("data:"):
            return ""
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return ""
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("A model stream sent an unreadable line; skipping it")
            return ""
        if not isinstance(chunk, dict):
            return ""

        if chunk.get("usage"):
            self.usage = chunk["usage"]
        text = ""
        for choice in chunk.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if delta.get("content"):
                text += delta["content"]
                self._text.append(delta["content"])
            if delta.get("reasoning_content"):
                self._reasoning.append(delta["reasoning_content"])
            for fragment in delta.get("tool_calls") or []:
                self._add_call_fragment(fragment)
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
        return text

    def _add_call_fragment(self, fragment: dict[str, Any]) -> None:
        call = self._calls.setdefault(
            int(fragment.get("index") or 0),
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if fragment.get("id"):
            call["id"] = fragment["id"]
        function = fragment.get("function") or {}
        # Named once. Concatenating would double a name a server repeats on every
        # fragment, and a server that splits one name across fragments is not
        # known to exist.
        if function.get("name") and not call["function"]["name"]:
            call["function"]["name"] = function["name"]
        if function.get("arguments"):
            call["function"]["arguments"] += function["arguments"]

    def body(self) -> dict[str, Any]:
        message: dict[str, Any] = {"content": "".join(self._text)}
        if self._reasoning:
            message["reasoning_content"] = "".join(self._reasoning)
        if self._calls:
            message["tool_calls"] = [self._calls[index] for index in sorted(self._calls)]
        return {
            "choices": [{"message": message, "finish_reason": self.finish_reason}],
            "usage": self.usage or {},
        }


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    #: See the module docstring: the four knobs a vendor subclass may turn.
    _max_tokens_field: ClassVar[str] = "max_completion_tokens"
    _reasoning_field: ClassVar[str | None] = None
    _STREAMING: ClassVar[bool] = True
    _STREAM_USAGE: ClassVar[bool] = True

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
        #: the engineering. There is nothing to probe here -- this endpoint
        #: family publishes no capability list -- so a model that cannot see
        #: answers with a 400, which `look` reports rather than swallowing.
        self._vision_model = (vision_model or "").strip() or None
        self._timeout = timeout_seconds
        # Learned on first use, then remembered: see `_structured`.
        self._json_schema_supported = True
        # Learned the same way: see `stream_chat`.
        self._streaming_supported = self._STREAMING

    def _plan_reasoning(
        self, *, effort: str | None, messages: list[dict[str, Any]] | None
    ) -> Reasoning:
        """What this call asks of the model's reasoning. A plain endpoint asks nothing.

        `effort` is the structured-output hint (`complete`/`look`, and a chat call
        with no tools); `None` means an agent step that may call tools, which
        `messages` is the transcript of. A subclass answers
        from its own configuration -- a method returning a value, because the
        provider is shared across threads and per-call state would race.
        """
        return Reasoning()

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        # Self-hosted servers (vLLM, llama.cpp) usually need no key.
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

    # -- requests -----------------------------------------------------------

    def _structured_payload(
        self, system: str, user: str, schema: type[T], max_tokens: int, effort: str
    ) -> dict[str, Any]:
        """One structured-output request, in whichever dialect this server takes.

        `json_schema` is the one that actually constrains decoding, so it is
        tried first and kept whenever it works. Not every OpenAI-compatible
        endpoint has it: DeepSeek documents `text` and `json_object` only and
        answers 400 "This response_format type is unavailable now" for the
        rest. The fallback asks for `json_object` -- which does guarantee
        syntactically valid JSON -- and puts the schema in the system message.
        Pydantic still validates the result either way, so the guarantee that
        matters (nothing malformed reaches the caller) is unchanged; what is
        lost is the server refusing to emit a wrong shape in the first place.
        """
        json_schema = strictify(schema.model_json_schema())
        plan = self._plan_reasoning(effort=effort, messages=None)
        payload: dict[str, Any] = {
            **plan.fields,
            "model": self._model,
            self._max_tokens_field: max_tokens + plan.extra_tokens,
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

    def _chat_payload(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> dict[str, Any]:
        # A step with no tools is a plain generation -- a title, a summary, the
        # note written when the rounds run out -- not a decision about what to
        # do next, so it is hinted like structured output and does not reason.
        # `None` is reserved for the step that chooses a tool.
        plan = self._plan_reasoning(effort=None if tools else "low", messages=messages)
        payload: dict[str, Any] = {
            **plan.fields,
            "model": self._model,
            self._max_tokens_field: max_tokens + plan.extra_tokens,
            "messages": [
                {"role": "system", "content": system},
                *_to_wire(
                    messages,
                    reasoning_field=self._reasoning_field if plan.enabled else None,
                ),
            ],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    # -- transport ----------------------------------------------------------

    def _wait(self, attempt: int, response: httpx.Response | None) -> None:
        """Sleep before the next attempt: the server's `Retry-After`, else backoff."""
        delay = RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S)) - 1]
        asked = _retry_after(response)
        if asked is not None:
            delay = max(delay, asked)
        time.sleep(min(delay, MAX_RETRY_WAIT_S))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, HTTP_ATTEMPTS + 1):
            last = attempt == HTTP_ATTEMPTS
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
                if status == 402:
                    raise LLMUnavailable(
                        "The provider reports the account has no balance (HTTP 402). "
                        "Top it up, then try again."
                    ) from exc
                error_text = exc.response.text
                if (
                    status == 400
                    and "response_format" in payload
                    and any(marker in error_text.lower() for marker in _RESPONSE_FORMAT_REJECTIONS)
                ):
                    raise _ResponseFormatUnsupported(error_text[:200]) from exc
                if status in RETRYABLE_STATUSES and not last:
                    logger.warning(
                        "Chat completion got %s; retrying %d of %d", status, attempt + 1, HTTP_ATTEMPTS
                    )
                    self._wait(attempt, exc.response)
                    continue
                if status in BUSY_STATUSES:
                    raise LLMBusy(
                        f"The model service is too busy to answer ({_describe_busy(status)}). "
                        "It was asked again and still could not take the request.",
                        retry_after_s=_retry_after(exc.response),
                    ) from exc
                raise LLMError(f"Chat completion failed ({status}): {error_text[:200]}") from exc
            except httpx.HTTPError as exc:
                if not last:
                    logger.warning(
                        "Chat completion request failed (%s); retrying %d of %d",
                        exc,
                        attempt + 1,
                        HTTP_ATTEMPTS,
                    )
                    self._wait(attempt, None)
                    continue
                raise LLMError(f"Chat completion request failed: {exc}") from exc

            try:
                body = dict(response.json())
            except ValueError as exc:
                raise LLMError("The model server answered with something that is not JSON.") from exc
            reason = _finish_reason(body)
            if reason in INTERRUPTED_FINISH_REASONS:
                if not last:
                    logger.warning(
                        "The server stopped generating early (%s); retrying %d of %d",
                        reason,
                        attempt + 1,
                        HTTP_ATTEMPTS,
                    )
                    self._wait(attempt, None)
                    continue
                raise LLMBusy(
                    f"The provider stopped generating before the answer was finished ({reason}). "
                    "That is the server being short of capacity -- try again shortly."
                )
            _log_prompt_cache(body.get("usage"))
            return body
        raise AssertionError("unreachable: the final attempt returns or raises")

    # -- structured output --------------------------------------------------

    def _structured(
        self, build: Callable[[], dict[str, Any]], schema: type[T], *, refusal: str
    ) -> Completion[T]:
        """Send a structured request and validate the answer, with one repair attempt.

        `build` makes the request afresh each time because what it contains
        depends on state this method changes: whether `json_schema` is
        available, learned from the first rejection. The shared body of
        `complete` and `look`, which differ only in what the request holds.

        An empty or invalid answer is asked for once more with the defect named
        -- a retry that repeats the request draws another sample from the same
        distribution, one that names the problem is an easier question. A
        hosted JSON mode is not constrained decoding (DeepSeek documents that
        it "may occasionally return empty content"), so this is a routine path,
        not a rare one. Never a third attempt.
        """
        usage = TokenUsage()
        problem = ""

        def request() -> dict[str, Any]:
            payload = build()
            if problem:
                payload["messages"].append({"role": "user", "content": _repair_message(problem)})
            return payload

        for attempt in range(1, STRUCTURED_ATTEMPTS + 1):
            try:
                body = self._post(request())
            except _ResponseFormatUnsupported:
                if not self._json_schema_supported:
                    raise LLMError(_NO_STRUCTURED_OUTPUT, usage=usage) from None
                # The endpoint speaks the chat API but not schema-constrained
                # output. Remembered, so this costs one 400 per process and not
                # one per call.
                self._json_schema_supported = False
                try:
                    body = self._post(request())
                except _ResponseFormatUnsupported:
                    raise LLMError(_NO_STRUCTURED_OUTPUT, usage=usage) from None
                except LLMError as exc:
                    _carrying(exc, usage)
                    raise
            except LLMError as exc:
                # The repair attempt failed at the transport after the first
                # attempt was answered and billed: that spend travels with the
                # failure instead of vanishing with it (ROAD_TO_10 1.4).
                _carrying(exc, usage)
                raise

            usage += _usage(body)
            choices = body.get("choices") or []
            if not choices:
                raise LLMError("The model returned no choices.", usage=usage)
            choice = choices[0]
            if choice.get("finish_reason") == "content_filter":
                raise LLMRefusal(refusal, usage=usage)
            if choice.get("finish_reason") == "length":
                raise LLMError(
                    "The model hit the output limit before finishing. Raise AI_MAX_TOKENS "
                    "(or AI_REASONING_BUDGET for a reasoning model).",
                    usage=usage,
                )

            content = _unfence((choice.get("message") or {}).get("content") or "")
            if not content.strip():
                problem = "The model returned an empty response."
            else:
                try:
                    return Completion(value=schema.model_validate_json(content), usage=usage)
                except ValidationError as exc:
                    problem = (
                        f"The model returned output that does not match the expected schema: {exc}"
                    )
            if attempt < STRUCTURED_ATTEMPTS:
                logger.warning(
                    "Structured answer for %s failed (%s); retrying %d of %d with the problem "
                    "fed back",
                    schema.__name__,
                    problem.splitlines()[0],
                    attempt,
                    STRUCTURED_ATTEMPTS - 1,
                )
        raise LLMError(problem, usage=usage)

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        effort: str,
        max_tokens: int,
    ) -> Completion[T]:
        return self._structured(
            lambda: self._structured_payload(system, user, schema, max_tokens, effort),
            schema,
            refusal="The provider's content filter rejected this request.",
        )

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

        def build() -> dict[str, Any]:
            payload = self._structured_payload(system, user, schema, max_tokens, effort)
            payload["model"] = self._vision_model or self._model
            payload["messages"][-1]["content"] = [
                *(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_media_type(one)};base64,"
                            + base64.b64encode(one).decode("ascii")
                        },
                    }
                    for one in images
                ),
                # The question after the pictures: the parts are read in order,
                # and one asked first is asked about nothing.
                {"type": "text", "text": payload["messages"][-1]["content"]},
            ]
            return payload

        return self._structured(
            build, schema, refusal="The provider's content filter rejected this render."
        )

    # -- agent steps --------------------------------------------------------

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
                    # Gemini-only today (see `ToolCall.provider_extra`); any
                    # other endpoint simply has no `extra_content` to carry.
                    provider_extra=raw.get("extra_content") or None,
                )
            )
        reasoning = message.get(self._reasoning_field) if self._reasoning_field else None
        return AssistantTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            usage=_usage(body),
            truncated=choice.get("finish_reason") == "length",
            reasoning=reasoning if isinstance(reasoning, str) else None,
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

    def stream_chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Iterator[ChatEvent]:
        """The same request with `stream: true`, yielding text as it is written.

        The deltas are for display only: `Finished.turn` is assembled from the
        whole stream and is the answer, tool calls included.

        **A stream that fails part-way falls back to the whole request rather
        than raising.** `_post`'s retry cannot help here -- some of the answer
        has already been shown, and re-issuing would double it -- so a broken
        stream is repeated once as a *non-streaming* call, whose text replaces
        what was shown rather than appending to it. That is why the contract is
        that `Finished.turn.text` is the answer and the deltas never were.
        Errors the whole request raises properly (a rejected key, no balance)
        therefore surface from it, with their words, and not from here.

        A server that rejects the streaming form itself (a 400, 404 or 422
        where the plain request then works) is remembered and not asked again.
        """
        if not self._streaming_supported:
            yield Finished(
                self.chat(system=system, messages=messages, tools=tools, max_tokens=max_tokens)
            )
            return

        payload = self._chat_payload(system, messages, tools, max_tokens)
        payload["stream"] = True
        if self._STREAM_USAGE:
            payload["stream_options"] = {"include_usage": True}

        assembled = _StreamAssembler()
        rejected_with: int | None = None
        broke = False
        try:
            with httpx.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    delta = assembled.feed(line)
                    if delta:
                        yield TextDelta(delta)
        except httpx.HTTPError as exc:
            broke = True
            if isinstance(exc, httpx.HTTPStatusError):
                rejected_with = exc.response.status_code
            logger.warning("A model stream failed (%s); falling back to a whole request", exc)

        if not assembled.finished or assembled.finish_reason in INTERRUPTED_FINISH_REASONS:
            if not broke:
                logger.warning("A model stream ended without a usable finish; repeating it whole")
            turn = self.chat(system=system, messages=messages, tools=tools, max_tokens=max_tokens)
            if rejected_with in (400, 404, 422):
                self._streaming_supported = False
                logger.warning(
                    "This endpoint rejected the streaming form (%s) but answers the plain "
                    "one; it will not be asked to stream again",
                    rejected_with,
                )
            yield Finished(turn)
            return

        _log_prompt_cache(assembled.usage)
        yield Finished(self._parse_turn(assembled.body()))
