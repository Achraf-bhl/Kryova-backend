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
import logging
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.provider import (
    AssistantTurn,
    Completion,
    LLMError,
    LLMProvider,
    LLMUnavailable,
    TokenUsage,
    ToolCall,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _usage(payload: dict[str, Any]) -> TokenUsage:
    """Ollama reports counts at the top level under its own names."""
    return TokenUsage(
        prompt_tokens=int(payload.get("prompt_eval_count") or 0),
        completion_tokens=int(payload.get("eval_count") or 0),
    )


# Ollama has no effort parameter. Map the hint onto a token ceiling for the
# model's own reasoning so the knob still means something locally.
_EFFORT_PREDICT: dict[str, int] = {
    "low": 1_024,
    "medium": 4_096,
    "high": 8_192,
    "xhigh": 16_384,
    "max": 32_768,
}

#: Ollama's own default context window when a request does not set `num_ctx`.
#: Not a number we choose -- a number we have to defend against. See
#: `_context_window`.
OLLAMA_DEFAULT_NUM_CTX = 4_096

#: Ceiling on the window we will ask for, regardless of what the model claims
#: to support. gpt-oss:20b advertises 131072; allocating that much KV cache
#: pushes the model off the GPU on any consumer card and turns a 4-second turn
#: into a 4-minute one. 32k comfortably holds the agent's system prompt, all 26
#: tool schemas and a long CATIA session.
MAX_CONTEXT_WINDOW = 32_768

#: Never ask for less than this, even from a small model: the agent's system
#: prompt plus its tool schemas is ~6k tokens before the user has said anything.
MIN_CONTEXT_WINDOW = 8_192

#: Attempts for one agent step. Two: a transient parser failure clears on a
#: fresh sample, and a third try would only add latency to a turn the user is
#: already waiting on.
CHAT_ATTEMPTS = 2

#: Pause before the retry. Short -- Ollama is a local process, so this is not
#: backing off a rate limit, just not hammering a server mid-hiccup.
RETRY_BACKOFF_S = 0.5


def _refuse_if_truncated(body: dict[str, Any], num_ctx: int) -> None:
    """Turn a silently truncated prompt into a loud failure.

    Even with the window sized to the model, a long enough CATIA session can
    still outgrow it. Ollama's response says nothing when that happens -- the
    call returns 200 with a confident answer built on a transcript missing its
    first half. `prompt_eval_count` is the one observable: it reports how many
    tokens actually reached the model, so a count pinned at the window is the
    signature of a prompt that did not fit.

    Raising here is the point. `LLMError` is already what the route renders as
    "the model could not answer", which is true and recoverable; the previous
    behaviour was an answer that looked fine and was reasoning about a
    conversation that had been cut in half.
    """
    used = int(body.get("prompt_eval_count") or 0)
    # Ollama shaves a little off the window for the generation buffer, so an
    # exact equality test would miss most real truncations.
    if used and used >= num_ctx - 64:
        raise LLMError(
            f"The conversation no longer fits the model's {num_ctx}-token context "
            f"window ({used} tokens sent), so the oldest messages were dropped "
            "before the model saw them. Start a new conversation, or switch to a "
            "model with a larger window."
        )


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.model = model
        self._timeout = timeout_seconds
        #: Resolved lazily from /api/show and cached: one HTTP round trip per
        #: process, not one per agent step.
        self._num_ctx: int | None = None

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
            model.get("name", "").split(":")[0] for model in response.json().get("models", [])
        }
        if self._model.split(":")[0] not in installed:
            raise LLMUnavailable(
                f"Ollama is running but the model '{self._model}' is not installed. "
                f"Run `ollama pull {self._model}`."
            )

    def _context_window(self) -> int:
        """The `num_ctx` to ask for, resolved from the model itself.

        This exists because of the single most damaging default in the stack.
        **Ollama does not size the context window to the model.** A request that
        omits `num_ctx` gets 4096 tokens no matter what the model supports, and
        a prompt longer than that is *silently truncated from the front* -- no
        error, no flag in the response, nothing above this line can tell.

        For a chat app that is a slow memory leak. For this agent it is fatal,
        because the agent's prompt starts at roughly 6k tokens before the user
        has typed anything: a ~2k system prompt plus ~4k of schemas for 26
        tools. Every symptom that follows was observed live and blamed on the
        model being small:

        - it re-asked for dimensions given one message earlier -- the turn
          holding them had been truncated away;
        - it invented tool names -- the schemas naming the real ones were
          partly cut;
        - it emitted tool calls as prose while claiming work was done -- with
          the front of the harmony prompt gone, it was no longer answering in
          the tool-call channel at all.

        Measured on gpt-oss:20b: a realistic transcript of 8997 tokens arrived
        as 3900, and the model returned empty content and no tool calls. The
        same request with `num_ctx` set answered correctly.

        So: read the model's real limit, clamp it into a range that is useful
        without evicting the model from the GPU, and never leave it unset. A
        model that will not tell us gets `MIN_CONTEXT_WINDOW`, which is still
        twice Ollama's default.
        """
        if self._num_ctx is not None:
            return self._num_ctx

        advertised = 0
        try:
            response = httpx.post(
                f"{self._base_url}/api/show", json={"model": self._model}, timeout=10.0
            )
            response.raise_for_status()
            info = response.json().get("model_info") or {}
            # Keyed by architecture -- `gptoss.context_length`,
            # `qwen2.context_length` -- so find it by suffix rather than
            # hardcoding the family.
            advertised = max(
                (int(v) for k, v in info.items() if k.endswith(".context_length") and v),
                default=0,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            # Sizing the window is a best effort; a model that will not
            # introspect still gets a floor well above Ollama's default.
            logger.warning(
                "Could not read the context length of %r from Ollama; using %d",
                self._model,
                MIN_CONTEXT_WINDOW,
            )

        self._num_ctx = max(MIN_CONTEXT_WINDOW, min(advertised or 0, MAX_CONTEXT_WINDOW))
        logger.info("Ollama context window for %r: %d tokens", self._model, self._num_ctx)
        return self._num_ctx

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
                # Room for the answer as well as the prompt: `num_ctx` covers
                # both, so a window sized to the prompt alone truncates it by
                # exactly the length of the reply.
                "num_ctx": max(
                    self._context_window(),
                    _EFFORT_PREDICT.get(effort, 4_096) + max_tokens,
                ),
            },
        }

        try:
            response = httpx.post(f"{self._base_url}/api/chat", json=payload, timeout=self._timeout)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama did not respond within {self._timeout:g}s. A larger model on "
                "CPU can exceed this -- raise AI_TIMEOUT_SECONDS or use a smaller model."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        body = response.json()
        content = body.get("message", {}).get("content", "")
        if not content.strip():
            raise LLMError("Ollama returned an empty response.")

        try:
            return Completion(value=schema.model_validate_json(content), usage=_usage(body))
        except ValidationError as exc:
            # Schema-constrained decoding makes this rare, but a small quantised
            # model can still stop early and truncate the JSON.
            raise LLMError(
                f"Ollama returned output that does not match the expected schema: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMError("Ollama returned malformed JSON.") from exc

    def _shrink_window_after_oom(self, exc: httpx.HTTPStatusError, payload: dict[str, Any]) -> bool:
        """Halve the context window when the GPU could not fit it, and say so.

        The window this provider asks for has to suit whichever model the user
        configured, and the ceiling that is right for one is fatal for another.
        Measured on an RTX 5070 Laptop (8 GB): `gpt-oss:20b` runs happily at
        32768, and `qwen3-coder:30b` refuses to start at all --

            llama-server reported out-of-memory during startup: CUDA error

        -- while working at 16384. A fixed ceiling therefore means that changing
        `AI_MODEL` to a bigger model breaks every AI request with an error about
        CUDA, which tells the user nothing about what to do.

        Halving and retrying costs one model load on the first request after a
        model change and nothing afterwards, because `_context_window` caches
        the result. It stops at `MIN_CONTEXT_WINDOW`: below that the agent's own
        prompt no longer fits, and a window too small to hold the tool schemas
        makes the model write its tool calls as prose instead of calling them
        (observed on this model at 8192), which is a worse failure than an
        honest error.
        """
        detail = ""
        try:
            detail = str(exc.response.json().get("error", ""))
        except ValueError:
            detail = exc.response.text[:200]
        if "out of memory" not in detail.lower():
            return False

        current = int(payload.get("options", {}).get("num_ctx") or self._context_window())
        reduced = max(MIN_CONTEXT_WINDOW, current // 2)
        if reduced >= current:
            raise LLMError(
                f"Ollama ran out of GPU memory loading {self._model!r} even at the "
                f"smallest workable context window ({current} tokens). Use a smaller "
                "model, or free GPU memory and try again."
            ) from exc

        logger.warning(
            "Ollama ran out of GPU memory for %r at num_ctx=%d; retrying at %d",
            self._model,
            current,
            reduced,
        )
        self._num_ctx = reduced
        payload.setdefault("options", {})["num_ctx"] = reduced
        return True

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one agent step, retrying a transient server-side failure.

        Ollama returns 500 on a request whose generation *succeeded*. Seen live
        twice in ~40 agent turns, with the slot log showing the tokens produced
        normally (`truncated = 0`) immediately before the 500 -- the failure is
        in Ollama's own parsing of the finished output, not in the model or the
        prompt. Sampling at temperature 1.0 means the retry draws a different
        completion, which is exactly what clears it.

        Retrying is safe because `/api/chat` has no side effects: it mutates
        nothing, and a repeat is a fresh sample rather than a duplicated action.
        Only 5xx and connection failures are retried -- a 4xx is a bug in what
        we sent and will fail identically forever, and a timeout has already
        spent the caller's patience.
        """
        last: Exception | None = None
        attempt = 0
        while attempt < CHAT_ATTEMPTS:
            try:
                response = httpx.post(
                    f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
                )
                response.raise_for_status()
                return dict(response.json())
            except httpx.TimeoutException as exc:
                raise LLMError(f"Ollama did not respond within {self._timeout:g}s.") from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise LLMError(f"Ollama rejected the request: {exc}") from exc
                # A window that will not fit is not a transient failure, so it
                # does not spend the transient budget. Halving is bounded on its
                # own -- it stops at MIN_CONTEXT_WINDOW -- and letting it consume
                # an attempt would mean a model needing two halvings never got
                # its actual request sent.
                if self._shrink_window_after_oom(exc, payload):
                    continue
                last = exc
            except httpx.HTTPError as exc:
                last = exc
            attempt += 1
            if attempt < CHAT_ATTEMPTS:
                logger.warning(
                    "Ollama chat failed (%s); retrying %d of %d", last, attempt + 1, CHAT_ATTEMPTS
                )
                time.sleep(RETRY_BACKOFF_S * attempt)
        raise LLMError(f"Ollama request failed after {CHAT_ATTEMPTS} attempts: {last}")

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        """Ollama speaks the OpenAI message and tool shape natively."""
        num_ctx = self._context_window()
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, *messages],
            # `num_ctx` is not optional here -- see `_context_window`. Without
            # it the agent's prompt is silently cut to 4096 tokens and the loop
            # runs on a transcript the model cannot see.
            "options": {"num_predict": max_tokens, "num_ctx": num_ctx},
        }
        if tools:
            payload["tools"] = tools

        body = self._post_chat(payload)
        _refuse_if_truncated(body, num_ctx)
        message = body.get("message") or {}
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
        return AssistantTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            usage=_usage(body),
            # Ollama stops on `num_predict` without saying so in a dedicated
            # field; `done_reason` is the closest thing it reports.
            truncated=body.get("done_reason") == "length",
        )
