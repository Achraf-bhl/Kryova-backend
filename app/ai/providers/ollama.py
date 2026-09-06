"""Ollama provider -- local inference, nothing leaves the machine.

**The test-phase default, not the production story** (decided 2026-09-05):
production runs a hosted API and will not support Ollama. This provider stays
for two reasons that survive that decision -- it is the free, keyless loop every
developer and CI run uses, and it is the ready-made answer if a customer's
security review ever demands a deployment where geometry cannot leave the
building. Neither reason makes it what customers get by default.

Ollama constrains generation to a JSON Schema via the `format` field, so the
structured-output contract in `provider.py` holds here the same as it does for
a hosted API.
"""

import base64
import json
import logging
import os
import time
from collections.abc import Sequence
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
    VisionUnsupported,
)
from app.ai.providers._json_schema import local_decoding_problem

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

#: How many transformer layers to put on the GPU. `None` leaves the decision to
#: Ollama, which is the safe default for a machine nobody has measured.
#:
#: **Measured on the Windows seat, 2026-09-06, and the difference is not
#: marginal.** An 8 GB RTX 5070 Laptop shared with CATIA, qwen3.5:9b, 34
#: layers:
#:
#:     num_ctx  Ollama's own choice        forced to all 34 layers
#:     32768    76% GPU, 25.7 tok/s        100% GPU, 59.0 tok/s
#:     24576    79% GPU, 31.3 tok/s        100% GPU, 58.5 tok/s
#:     16384    84% GPU, 37.9 tok/s        100% GPU, 57.7 tok/s
#:
#: Ollama offloaded 33 of 34 layers and kept one on the CPU, because its
#: estimator holds a margin against a card it is sharing. One layer on the CPU
#: is not one thirty-fourth of the cost: every token crosses the bus twice, and
#: the model ran at 43% of its speed. A CATIA build is tens of turns, so this
#: is the difference between a usable product and a demo.
#:
#: It is opt-in because the risk is real in the other direction: forcing more
#: layers than the card can hold makes Ollama fail the load, and
#: `_retry_with_smaller_window` is what catches that. A machine that has not
#: been measured is better off with Ollama's guess.
GPU_LAYERS_ALL = "all"

#: What `all` sends. Larger than any model's layer count, because llama.cpp
#: clamps `n_gpu_layers` to the real total -- see `_gpu_layers` for why this is
#: a sentinel rather than a count read from the model's metadata.
EVERY_LAYER = 999

#: Prompt tokens one 1024x768 render is assumed to cost, for sizing `num_ctx`
#: on a vision call. An estimate and deliberately a generous one: a vision model
#: tiles an image and the count depends on the projector, and getting this *low*
#: has the one consequence this file exists to prevent — Ollama truncates the
#: prompt from the front, silently, and the model answers about an image it was
#: never shown. Over-allocating costs KV cache; under-allocating costs the
#: truth.
IMAGE_PROMPT_TOKENS = 1_600

#: How many times a structured answer is asked for again when the first one
#: came back empty or did not match its schema, with the problem fed back as
#: the next user message. One. Phase 16.4's rule -- diagnose, repair, bounded
#: retry -- applied at the smallest scale there is: a second attempt that sees
#: what was wrong with the first is a different request, a third that does not
#: is the same one. Measured on ladder prompt H4 (2026-09-06): three failures
#: in a row at 146 s each, none of which was told what the previous one did.
STRUCTURED_ATTEMPTS = 2

#: Pause before the retry. Short -- Ollama is a local process, so this is not
#: backing off a rate limit, just not hammering a server mid-hiccup.
RETRY_BACKOFF_S = 0.5


def _log_generation_speed(model: str, body: dict[str, Any], wall_seconds: float) -> None:
    """How fast the model actually generated, from its own counters.

    Ollama reports `eval_count` and `eval_duration` per response, which is the
    only honest measure of whether the model is on the GPU: wall time mixes in
    the prompt, the queue and the network, and a slow turn on a fast model
    looks the same as a fast turn on a slow one.

    This is what makes an offload regression visible. Measured on the seat,
    the same model was 25.7 tok/s with one layer on the CPU and 59.0 tok/s
    with none -- a number nobody was printing, on a defect that survived
    weeks of use because every turn merely felt slow.

    Never raises and never blocks: a missing counter means no line, not an
    error on the agent's path.
    """
    try:
        tokens = int(body.get("eval_count") or 0)
        nanoseconds = int(body.get("eval_duration") or 0)
    except (TypeError, ValueError):
        return
    if not tokens or not nanoseconds:
        return
    rate = tokens / (nanoseconds / 1e9)
    logger.info(
        "%s generated %d tokens at %.1f tok/s (%.1f s wall)",
        model,
        tokens,
        rate,
        wall_seconds,
    )


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


#: Sent on every schema-constrained call. `qwen3.5:9b` is a thinking model, and
#: Ollama keeps reasoning in `message.thinking` while the JSON grammar applies
#: only to `message.content` -- so the two compete for one `num_predict` budget
#: and the reasoning always goes first.
#:
#: Measured on the seat, 2026-09-06, ladder prompt H4 run 11. The same request,
#: same schema, same model:
#:
#:     thinking on,  num_predict 1024 -> 20.7 s, 1024 tokens of thinking,
#:                                       content '', done_reason 'length'
#:     thinking on,  num_predict 4096 -> 82.0 s, 4220 tokens, valid answer
#:     think: false, num_predict 1024 ->  8.9 s,  280 tokens, valid answer
#:
#: Nine times faster and correct. Reasoning buys nothing here anyway: the
#: grammar already forces the shape, and what is left is reading two numbers
#: out of a sentence. This is also the whole history of the drafting defect --
#: the 146 s attempts were a thinking model against a 13,510-character grammar,
#: and the 42 s "empty response" that followed the flat schema was the thinking
#: budget being spent before the answer began.
#:
#: `chat` deliberately does not set it: choosing a tool is exactly the decision
#: reasoning helps with, and it is not competing with a grammar there.
THINKING_OFF = False

def _no_content_reason(body: dict[str, Any]) -> str:
    """Why `message.content` is empty, in words that name the actual remedy.

    "Ollama returned an empty response" was reported for three different
    conditions, and the one that kept happening was the one it described worst:
    a thinking model that spent its whole token budget reasoning and stopped
    before writing any JSON. That reads as "the model failed" when it is
    "the model was cut off", and the two have different fixes.
    """
    reason = str(body.get("done_reason") or "")
    thinking = str((body.get("message") or {}).get("thinking") or "")
    if reason == "length":
        if thinking:
            return (
                "Ollama stopped at its token limit while the model was still "
                f"reasoning ({len(thinking.split())} words of it) and never began "
                "the answer. Reasoning and the answer share one budget, so raise "
                "num_predict or turn thinking off for this call."
            )
        return (
            "Ollama stopped at its token limit before the answer was complete. "
            "Raise num_predict, or ask for less in one call."
        )
    if thinking:
        return (
            "Ollama returned reasoning but no answer, which is a model that "
            "stopped between the two. Retrying is the right response."
        )
    return "Ollama returned an empty response."


def _answer_budget(effort: str, max_tokens: int) -> int:
    """How many tokens a structured answer may run to.

    The effort level's own budget, capped by the caller's. `max_tokens` is the
    product-wide ceiling (8,000) and was being sent as `num_predict` for every
    structured call; a 9B model walking a grammar it cannot satisfy generates
    until it hits that ceiling, which at ~30 tokens/s is the 146 s measured on
    ladder prompt H4. A load case is a few hundred tokens of JSON; "low"
    effort's 1,024 is room for three of them.
    """
    return max(1, min(max_tokens, _EFFORT_PREDICT.get(effort, max_tokens)))


def _repair_message(problem: str) -> str:
    """The second attempt's brief: what was wrong, and the one rule that fixes it."""
    first_line = problem.splitlines()[0] if problem else "The answer was empty."
    return (
        f"Your previous answer could not be used: {first_line} Answer again with "
        "only the JSON object, every required field present, no field left as a "
        "placeholder, and nothing outside the object."
    )


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        vision_model: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.model = model
        #: Which model the visual check runs against. Separate because it
        #: usually *is* separate: the shipping default (`qwen2.5-coder`) writes
        #: CAD operations and has no eyes at all, and the model that can see is
        #: a second pull. None means "try the main model", which is right for a
        #: hosted provider and answered honestly below when it is wrong.
        self._vision_model = vision_model
        self._timeout = timeout_seconds
        #: Resolved lazily from /api/show and cached: one HTTP round trip per
        #: process, not one per agent step.
        self._num_ctx: int | None = None
        self._num_gpu: int | None = None
        self._num_gpu_resolved = False
        #: Same, for the vision capability of `_vision_model or _model`.
        self._can_see: bool | None = None

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

    def _gpu_layers(self) -> int | None:
        """`num_gpu` for this model, or None to leave the split to Ollama.

        `AI_GPU_LAYERS` takes a number of layers, or the word `all`.

        `all` sends `EVERY_LAYER`, a number larger than any model has, because
        llama.cpp clamps `n_gpu_layers` to the model's own total. Counting the
        layers here was tried first and was wrong by one: `block_count` in
        `/api/show` reports 32 for qwen3.5:9b, and Ollama counts 34 -- the
        repeating blocks, the output layer, and one more it does not name in
        the metadata. `block_count + 1` left the model at 89% GPU and 48.6
        tok/s where 34 gives 100% and 58.0. A number that has to be derived
        from metadata to mean "all of it" is a number that will be wrong again
        on the next model; asking for more than exists cannot be.

        Measured on the seat, 2026-09-06, qwen3.5:9b at num_ctx=32768:

            num_gpu=33   89% GPU   48.6 tok/s
            num_gpu=34  100% GPU   58.0 tok/s
            num_gpu=99  100% GPU   58.9 tok/s
            num_gpu=999 100% GPU   58.5 tok/s
        """
        if self._num_gpu_resolved:
            return self._num_gpu
        self._num_gpu_resolved = True

        # `Settings` first, the environment as the documented fallback -- the
        # same shape `agent.max_steps` uses, and for the same reason: a knob
        # that silently does nothing is worse than no knob.
        from app.core.config import settings

        configured = getattr(settings, "ai_gpu_layers", None) or os.environ.get("AI_GPU_LAYERS")
        if not configured:
            return None
        text = str(configured).strip().lower()
        if text == GPU_LAYERS_ALL:
            self._num_gpu = EVERY_LAYER
            logger.info("Offloading every layer of %r to the GPU", self._model)
            return self._num_gpu
        try:
            self._num_gpu = max(0, int(text))
        except ValueError:
            logger.warning("Ignoring unusable AI_GPU_LAYERS value %r", configured)
        return self._num_gpu

    def _with_gpu_layers(self, options: dict[str, Any]) -> dict[str, Any]:
        """Add `num_gpu` to a request's options when one is configured."""
        layers = self._gpu_layers()
        if layers is not None:
            options["num_gpu"] = layers
        return options

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
        grammar = schema.model_json_schema()
        self._refuse_an_undecodable_schema(grammar, schema.__name__)
        messages: list[dict[str, Any]] = [
            # System first: the same ordering every hosted provider wants for
            # prefix caching, and Ollama reuses its own KV cache across calls
            # that share one.
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": messages,
            # Constrains decoding to the schema rather than asking politely.
            "format": grammar,
            # See THINKING_OFF: reasoning and the answer share one token budget,
            # and against a grammar the reasoning wins and the answer never
            # starts.
            "think": THINKING_OFF,
            "options": self._with_gpu_layers(
                {
                    "num_predict": _answer_budget(effort, max_tokens),
                    # Room for the answer as well as the prompt: `num_ctx`
                    # covers both, so a window sized to the prompt alone
                    # truncates it by exactly the length of the reply.
                    "num_ctx": max(
                        self._context_window(),
                        _EFFORT_PREDICT.get(effort, 4_096) + max_tokens,
                    ),
                }
            ),
        }

        problem = ""
        usage = TokenUsage()
        for attempt in range(STRUCTURED_ATTEMPTS):
            if problem:
                # The repair brief. A retry that repeats the request verbatim
                # draws another sample from the same distribution; one that
                # names the defect is a different, easier question.
                messages.append({"role": "user", "content": _repair_message(problem)})
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

            body = response.json()
            usage += _usage(body)
            content = body.get("message", {}).get("content", "")
            if not content.strip():
                problem = _no_content_reason(body)
            else:
                try:
                    return Completion(value=schema.model_validate_json(content), usage=usage)
                except ValidationError as exc:
                    # Schema-constrained decoding makes this rare, but a small
                    # quantised model can still stop early and truncate the JSON.
                    problem = (
                        "Ollama returned output that does not match the expected "
                        f"schema: {exc}"
                    )
                except json.JSONDecodeError:
                    problem = "Ollama returned malformed JSON."
            if attempt + 1 < STRUCTURED_ATTEMPTS:
                logger.warning(
                    "Structured answer for %s failed (%s); retrying %d of %d with the "
                    "problem fed back",
                    schema.__name__,
                    problem.splitlines()[0],
                    attempt + 1,
                    STRUCTURED_ATTEMPTS - 1,
                )
        raise LLMError(problem)

    @staticmethod
    def _refuse_an_undecodable_schema(grammar: dict[str, Any], name: str) -> None:
        """Fail in no time with the reason, rather than in minutes without one.

        Measured on ladder prompt H4 (2026-09-06): the solver's own `LoadCase`
        schema handed to qwen3.5:9b as a grammar cost 146 s per attempt and
        never produced a valid answer. The budget in `_json_schema.py` is the
        line; this is where a schema that crosses it is turned back with the
        number that put it over, before any GPU time is spent.
        """
        problem = local_decoding_problem(grammar, name=name)
        if problem is not None:
            raise LLMError(problem)

    def _sees(self) -> bool:
        """Whether the model a visual check would run against can take an image.

        **This is the guard the whole vision path is built around.** Ollama does
        not refuse an image handed to a text-only model — it drops it and
        answers anyway, so the reply is a confident description of nothing,
        arriving with no error and no flag. Against a check whose entire job is
        to say whether a part looks right, that is worse than having no check:
        it manufactures agreement.

        Two structural signals, no list of model names to go stale. `/api/show`
        publishes `capabilities` for exactly this question, and a multimodal
        model is the only kind that has a `projector_info` block at all — the
        projector *is* the vision encoder. Either one is a yes; neither is a no.
        """
        if self._can_see is not None:
            return self._can_see

        model = self._vision_model or self._model
        try:
            response = httpx.post(
                f"{self._base_url}/api/show", json={"model": model}, timeout=10.0
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(
                f"Ollama could not describe the model '{model}' at {self._base_url}. "
                f"Check that it is running and that the model is installed "
                f"(`ollama pull {model}`)."
            ) from exc

        capabilities = {str(one).lower() for one in (body.get("capabilities") or [])}
        self._can_see = "vision" in capabilities or bool(body.get("projector_info"))
        return self._can_see

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
        """A schema-constrained answer about one or more PNGs, locally.

        Ollama attaches images to the *message* as base64 strings, in order, and
        offers nowhere to caption one — so the order is load-bearing and the
        caller has named it in `user`.
        """
        model = self._vision_model or self._model
        if not images:
            raise LLMError("A visual check needs at least one image.")
        if not self._sees():
            raise VisionUnsupported(
                f"Ollama's model '{model}' reports no vision capability, so it cannot "
                "look at a render — and Ollama would drop the image and answer anyway "
                "rather than refuse. Pull a model that can see (for example "
                "`ollama pull llava`) and set AI_VISION_MODEL to it."
            )

        # Room for the pictures as well as the words. `num_ctx` covers the whole
        # prompt, and a render is worth far more tokens than the sentence
        # describing it — sizing this from the text alone is how the images get
        # cut off the front of a prompt that reports no error.
        window = max(
            self._context_window(),
            _EFFORT_PREDICT.get(effort, 4_096)
            + max_tokens
            + IMAGE_PROMPT_TOKENS * len(images),
        )
        grammar = schema.model_json_schema()
        self._refuse_an_undecodable_schema(grammar, schema.__name__)
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": user,
                    "images": [base64.b64encode(one).decode("ascii") for one in images],
                },
            ],
            "format": grammar,
            "think": THINKING_OFF,
            "options": self._with_gpu_layers({"num_predict": max_tokens, "num_ctx": window}),
        }

        try:
            response = httpx.post(f"{self._base_url}/api/chat", json=payload, timeout=self._timeout)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama did not respond within {self._timeout:g}s. A vision model "
                "reading several renders on CPU can exceed this -- raise "
                "AI_TIMEOUT_SECONDS or review fewer views."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        body = response.json()
        _refuse_if_truncated(body, window)
        content = body.get("message", {}).get("content", "")
        if not content.strip():
            raise LLMError(_no_content_reason(body))

        try:
            return Completion(value=schema.model_validate_json(content), usage=_usage(body))
        except ValidationError as exc:
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
            "options": self._with_gpu_layers({"num_predict": max_tokens, "num_ctx": num_ctx}),
        }
        if tools:
            payload["tools"] = tools

        started = time.perf_counter()
        body = self._post_chat(payload)
        _log_generation_speed(self._model, body, time.perf_counter() - started)
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
