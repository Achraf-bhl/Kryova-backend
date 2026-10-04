"""DeepSeek's hosted API -- OpenAI-shaped, with reasoning that has to be echoed back.

`api.deepseek.com` speaks the chat-completions shape, so nearly all of
`OpenAICompatibleProvider` applies. Five things do not, all read from DeepSeek's
own API reference (not measured against the live endpoint: this machine has no
outbound network, so each is pinned by `tests/test_deepseek_provider.py` against
the documented wire format and is a row in `docs/WINDOWS_VERIFICATION.md` THE
QUEUE until a run with a key confirms it).

**1. Thinking is on by default, and tool calls then need it echoed back.** With
`tools` in the request, "the `reasoning_content` must be fully passed back to the
API in all subsequent requests ... If your code does not correctly pass back
`reasoning_content`, the API will return a 400 error." The generic provider has no
such field, so pointed at DeepSeek it would work on the first step of an agent
run and fail on the second. Here the reasoning is read off every turn
(`AssistantTurn.reasoning`), stored with the message, and replayed.

**2. ... but only when every assistant turn of the current question has some to
give.** A turn made with thinking off, one the agent wrote itself (a stop
notice), or one stored before this existed has none, and a request that enables
thinking over such a tool chain is the 400 above. Turns before the newest user
message do not count: the guide says their reasoning is ignored once a new
question starts. So `_plan_reasoning` enables thinking for a step only when the
current chain can be echoed. Otherwise that
step runs with thinking off and says so in the log. The alternative -- sending an
empty `reasoning_content` for the gaps -- is a guess about what the server
accepts, and the choice here is deterministic.

**3. The output cap is `max_tokens`.** DeepSeek does not document
`max_completion_tokens`, which the generic provider sends, so `AI_MAX_TOKENS`
would not apply. The cap also counts reasoning ("64K in thinking mode"), so a
call that reasons is given `AI_REASONING_BUDGET` extra tokens of headroom, or the
answer is cut off after the thinking has used the cap.

**4. Structured output is `json_object` only.** The reference lists `text` and
`json_object`; there is no `json_schema`. The provider starts there instead of
paying a rejected request to find out, and the schema travels in the system
message -- the same fallback the generic provider learns.

**5. Reasoning is a per-call decision.** Our effort words map onto DeepSeek's
`low`/`high`/`max`, with `none`/`minimal`/`low` meaning *thinking off*: parsing a
sentence into a load case is reading, and a reasoning model spends its tokens
before it answers. An agent step uses `AI_EFFORT_CHAT`, and `AI_THINKING=false`
turns reasoning off everywhere.

The model is whatever `AI_MODEL` names (`deepseek-flash` and `deepseek-v4-pro`
are the documented ones); nothing here depends on which.
"""

import logging
from typing import Any, ClassVar, Final

from app.ai.providers.openai_compatible import (
    DEFAULT_REASONING_BUDGET,
    OpenAICompatibleProvider,
    Reasoning,
)

logger = logging.getLogger(__name__)

#: DeepSeek's hosted endpoint. Made the default so configuration is a key and a
#: model name, not a URL anyone has to remember correctly.
DEFAULT_BASE_URL: Final = "https://api.deepseek.com"

#: Used only when `AI_MODEL` is left empty. A default is not a pin: set
#: `AI_MODEL=deepseek-v4-pro` (or, after `AI_PROVIDER` changes, anything else).
DEFAULT_MODEL: Final = "deepseek-flash"

#: The effort an agent step runs at when `AI_EFFORT_CHAT` is not set. DeepSeek's
#: own default, so an unconfigured deployment behaves as the vendor documents.
DEFAULT_REASONING_EFFORT: Final = "high"

#: Our effort words in DeepSeek's. `None` is *thinking off*, not a level: the
#: API's `reasoning_effort` has no "none" that this provider relies on, and
#: `thinking.type = disabled` is the documented switch.
_EFFORT: Final[dict[str, str | None]] = {
    "none": None,
    "minimal": None,
    "low": None,
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}


def _level(effort: str) -> str | None:
    """DeepSeek's level for `effort`, or None for thinking off.

    An effort nobody mapped reasons at DeepSeek's default rather than silently
    switching thinking off: an unknown word is more likely a typo in a judgement
    task's setting than a request for none.
    """
    word = (effort or "").strip().lower()
    if not word:
        return DEFAULT_REASONING_EFFORT
    return _EFFORT.get(word, DEFAULT_REASONING_EFFORT)


def _every_assistant_turn_can_be_echoed(messages: list[dict[str, Any]]) -> bool:
    """Whether DeepSeek can be sent this transcript with thinking enabled.

    Within the current user turn it requires each assistant turn's
    `reasoning_content`. A turn that has a string for it -- even an empty one,
    which is a different fact from absent -- can be echoed; one that has `None`
    cannot. Only the turns after the newest user message count: DeepSeek's thinking-mode
    guide says that once a new question starts, earlier turns' reasoning "does not
    need to be passed back; even if passed to the API, it will be ignored". Holding
    the whole transcript to the rule would switch thinking off for as long as one
    old message without reasoning (a stop notice, a message stored before this
    column existed) stayed in the window, for no reason the API has.
    """
    last_user = max(
        (index for index, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    return all(
        isinstance(message.get("reasoning"), str)
        for message in messages[last_user + 1 :]
        if message.get("role") == "assistant"
    )


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek. Everything OpenAI-shaped, plus reasoning handled as it documents."""

    name = "deepseek"

    _max_tokens_field: ClassVar[str] = "max_tokens"
    _reasoning_field: ClassVar[str | None] = "reasoning_content"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        base_url: str | None = None,
        *,
        thinking: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
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
        # Documented: `json_object` and `text` only. Starting here saves the
        # rejected request the generic provider would pay once to learn it.
        self._json_schema_supported = False
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort
        self._reasoning_budget = reasoning_budget

    def _plan_reasoning(
        self, *, effort: str | None, messages: list[dict[str, Any]] | None
    ) -> Reasoning:
        """Thinking on or off for this call, and the headroom it needs.

        `effort` is the structured-output hint, which a chat call with no tools
        also gets; `None` is an agent step that may call tools, which uses the
        configured chat effort and also needs the transcript to be echo-able (see
        the module docstring, point 2).
        """
        level = _level(effort if effort is not None else self._reasoning_effort)

        if not self._thinking:
            level = None
        elif level is not None and messages is not None:
            if not _every_assistant_turn_can_be_echoed(messages):
                logger.info(
                    "Thinking is off for this step: an earlier assistant turn has no stored "
                    "reasoning, and DeepSeek rejects a tool-calling request that enables thinking "
                    "without it. It resumes once those turns leave the window."
                )
                level = None

        if level is None:
            return Reasoning(enabled=False, fields={"thinking": {"type": "disabled"}})
        return Reasoning(
            enabled=True,
            fields={"thinking": {"type": "enabled"}, "reasoning_effort": level},
            extra_tokens=self._reasoning_budget,
        )
