"""NVIDIA's hosted NIM endpoint — OpenAI-shaped, with reasoning on the side.

`integrate.api.nvidia.com/v1` speaks the chat-completions shape, so almost all
of `OpenAICompatibleProvider` applies unchanged. Four things do not, and each
was measured against the live endpoint rather than taken from the docs.

**1. Reasoning comes back in its own field.** A Nemotron reasoning model puts
its chain of thought in `message.reasoning_content` and its actual answer in
`message.content`. On a *completed* generation the two are cleanly separated —
the answer never contains the reasoning. That is worth stating because it is the
opposite of the more familiar `<think>…</think>` models, and it is why nothing
here strips tags.

**2. Except when the generation is cut off.** Truncate mid-reasoning and the
partial chain of thought appears in **both** fields: `finish_reason: "length"`
with `content` holding "Here's a thinking process: 1. Analyze User Input…".
Handed to the agent as `text`, that reaches the user as the assistant's reply —
the model thinking out loud, presented as an answer, with no sign anything went
wrong. `_parse_turn` drops it. This is the single reason this file exists rather
than three lines of config.

**3. The reasoning controls are non-standard fields.** `chat_template_kwargs`
and `reasoning_budget` are NVIDIA extensions, and this endpoint *rejects* what
it does not recognise — `400 Validation: Unsupported parameter(s)`. So they go
through `_extra_body`, which only this provider fills in, and they are never
sent to a generic endpoint.

**4. Thinking is worth turning off for structured output.** Measured on
`nemotron-3.5-lightning-30b-a3b`, one small `response_format: json_schema` call:
890 completion tokens and 20.2 s with thinking on, ~20 tokens and 1.8 s with it
off, for the same correct answer. `complete()` is schema-constrained parsing —
a sentence into a load case — where the shape is the whole job and there is
nothing to deliberate about. `chat()` keeps thinking on, because choosing the
right CATIA operation out of 201 is exactly the judgement that reasoning buys.

Tool calling works in both modes; that was checked, because it is the thing the
agent lives or dies on.
"""

from typing import Any, Final

from app.ai.provider import AssistantTurn
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

#: NVIDIA's hosted endpoint. Made the default so configuration is a key and a
#: model name, not a URL anyone has to remember correctly.
DEFAULT_BASE_URL: Final = "https://integrate.api.nvidia.com/v1"

#: A reasoning MoE that answers fast, does tool calling, and is free to use on
#: NVIDIA's developer tier. Overridable with AI_MODEL — every model on
#: `integrate.api.nvidia.com` is reachable through this same provider.
DEFAULT_MODEL: Final = "nvidia/nemotron-3.5-lightning-30b-a3b"

#: Ceiling on reasoning tokens per call. Not the same budget as the answer:
#: a Nemotron model will happily spend three thousand tokens deliberating over
#: "give the answer blue", so this is what stops a trivial turn costing a large
#: one. Generous enough for a real CATIA decision, short of a runaway.
DEFAULT_REASONING_BUDGET: Final = 4_096


class NvidiaProvider(OpenAICompatibleProvider):
    """NVIDIA NIM. Everything OpenAI-shaped, plus reasoning handled honestly."""

    name = "nvidia"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        base_url: str | None = None,
        *,
        thinking: bool = True,
        reasoning_budget: int = DEFAULT_REASONING_BUDGET,
        vision_model: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url or DEFAULT_BASE_URL,
            api_key=api_key,
            model=model or DEFAULT_MODEL,
            timeout_seconds=timeout_seconds,
            vision_model=vision_model,
        )
        self._thinking = thinking
        self._reasoning_budget = reasoning_budget
        #: Set for the duration of one `complete()` call. Structured output is
        #: parsing, not judgement, and reasoning through it costs ~45x the
        #: tokens for the same answer.
        self._thinking_now = thinking

    # -- request ------------------------------------------------------------

    def _extra_body(self) -> dict[str, Any]:
        """The two NVIDIA extension fields, and nothing else.

        `enable_thinking` is always sent, including when false: the model
        reasons by default, so leaving the field out is not the same as turning
        it off.
        """
        extras: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": self._thinking_now}}
        if self._thinking_now:
            extras["reasoning_budget"] = self._reasoning_budget
        return extras

    def _structured_payload(self, system: str, user: str, schema: Any, max_tokens: int) -> Any:
        """Build a structured-output request with thinking off for its duration.

        The flag is toggled around the call rather than passed down because
        `_extra_body` is the base class's hook and takes no arguments — and
        widening its signature for one vendor's cost optimisation would push
        NVIDIA's billing into the shape of every other provider's requests.
        """
        self._thinking_now = False
        try:
            return super()._structured_payload(system, user, schema, max_tokens)
        finally:
            self._thinking_now = self._thinking

    # -- response -----------------------------------------------------------

    def _parse_turn(self, body: dict[str, Any]) -> AssistantTurn:
        """Read a turn, refusing to pass chain of thought off as an answer.

        Two corrections to the base reading, both measured:

        * **Truncated mid-reasoning.** `content` then holds partial chain of
          thought rather than a partial answer, and there is no way to tell one
          from the other after the fact. So when the model was cut off with
          reasoning in flight, the text is dropped: the agent already appends
          "this answer was cut off" for a truncated turn, and an empty fragment
          with that note is a far better outcome than the model's private
          deliberation presented as its reply.
        * **Whitespace-only content beside a tool call.** With thinking off the
          model returns `"\n\n"` alongside `tool_calls`, and the agent yields
          any non-empty text as narration — so the UI would show an empty
          speech bubble before every tool ran.
        """
        turn = super()._parse_turn(body)

        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        reasoning = (message.get("reasoning_content") or "").strip()

        if turn.truncated and reasoning:
            turn.text = ""
        elif not turn.text.strip():
            turn.text = ""

        return turn
