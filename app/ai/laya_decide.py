"""Laya as the tool-family router: Jev's shape, run locally, for one decision.

`app/ai/tool_retrieval.py::decider_for` already had a `decide=` seam for naming
the tool family a request needs before the turn starts -- it has just always
been bound to the agent's own conversational provider, which means "which
family does this need" costs a full LLM call sharing the same slow generator
as everything else. Measured on this seat: qwen3.5:9b does not fit an 8GB
card at half that VRAM, so a single intent decision runs for minutes on top of
the turn it was meant to speed up.

Laya (ConvAI Innovations, Apache-2.0, `github.com/NandhaKishorM/laya`) is the
open-weight shape TypeSafe's Jev introduced (System One Models, 2026-09-15): a
421M non-autoregressive encoder that answers typed Choice/Score/Noul questions
over a state in one forward pass, with a calibrated probability per option --
never a token of generated text, so there is nothing here to parse and nothing
it can hallucinate outside the option set. Measured on this machine's GPU (a
4GB GTX 1650 Ti qwen3.5:9b does not fit): 141 ms for one Choice question,
against the 254-271 s a single intent decision cost through the LLM path.

**What it is not.** Laya cannot write a tool call's arguments, cannot hold a
conversation, and is the wrong tool for anything needing multi-step reasoning
-- routing "which family of operations does this message need" is exactly the
shallow, single-forward-pass judgment its own docs recommend it for, and
nothing here asks it for more. The agent's own provider still does everything
downstream of the family Laya names.

**Fails open, always.** A decider that cannot decide must not crash the turn
it was meant to speed up -- a missing GPU, a network hiccup on first load, an
incompatible checkpoint, all leave `_get_agent` returning `None` and this
module's `decide` returning `None`, which `tool_retrieval.select` reads as
"the lexical selection stands," exactly `decider_for`'s own contract.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from app.ai.tool_retrieval import INTENT_FAMILIES, IntentDecider

if TYPE_CHECKING:  # pragma: no cover - import kept out of the runtime path
    pass

logger = logging.getLogger(__name__)

#: The checkpoint this module has been measured against. A different one is a
#: different decider and should be a different `MODEL_ID`, not a silent swap.
MODEL_ID = "convaiinnovations/laya"
SUBFOLDER = "typed-decisions"

#: The label offered for "none of the named families." Distinct from any real
#: family name so a criteria dict collision is impossible.
_DECLINE = "none"

_agent: Any = None
_agent_failed = False
_agent_lock = threading.Lock()


def pick_device(requested: str, cuda_available: bool) -> str:
    """Resolve `AI_INTENT_ROUTER_DEVICE` to a torch device name.

    `auto` prefers CUDA when it is there. An explicit `cuda` on a machine that
    has none falls back to the CPU rather than failing the boot -- the router is
    an optimisation and this module fails open everywhere else. Anything that
    is not one of the three is a typo, and raising says so at startup instead
    of quietly running somewhere nobody chose.
    """
    choice = (requested or "auto").strip().lower()
    if choice not in ("auto", "cpu", "cuda"):
        raise ValueError(f"AI_INTENT_ROUTER_DEVICE must be auto, cpu or cuda, got {requested!r}")
    if choice == "cpu":
        return "cpu"
    return "cuda" if cuda_available else "cpu"


def _load_agent() -> Any:
    """Import torch/laya and load the checkpoint. Raises on any failure --
    callers decide what a failure means; this function only tries once."""
    import laya
    import torch

    from app.core.config import settings

    device = pick_device(settings.ai_intent_router_device, torch.cuda.is_available())
    agent = laya.load(MODEL_ID, subfolder=SUBFOLDER, device=device)
    logger.info("Laya intent router loaded on %s", device)
    return agent


def _get_agent() -> Any:
    """The lazy singleton. Loaded once per process, on first real use rather
    than at import time -- an import that downloads a checkpoint is how a
    plain `import app.ai.agent` in a test would reach the network.

    A load failure is cached (`_agent_failed`), not retried every call: torch
    or the checkpoint being unavailable is a per-process fact, not a transient
    one, and retrying it on every turn would make the failure as slow as the
    thing it was meant to replace.
    """
    global _agent, _agent_failed
    if _agent is not None or _agent_failed:
        return _agent
    with _agent_lock:
        if _agent is not None or _agent_failed:
            return _agent
        try:
            _agent = _load_agent()
        except Exception:
            logger.exception("Laya failed to load; the intent router is disabled")
            _agent_failed = True
    return _agent


def _hint(label: str) -> str:
    """A one-line description of a family, built from its own trigger words --
    reusing `INTENT_FAMILIES` rather than writing a second copy of what each
    family means. Falls back to the bare label for one `decide` does not own
    (there is currently exactly one: the `_DECLINE` option itself)."""
    entry = INTENT_FAMILIES.get(label)
    if entry is None:
        return label
    words, _tools = entry
    return ", ".join(words[:6])


def laya_decider(agent: Any = None) -> IntentDecider:
    """Bind Laya into the `decide=` seam `tool_retrieval.select` takes.

    `agent`, if given, is used instead of the lazy singleton -- the seam a
    test injects a fake through, so nothing here needs torch or a network
    call to be exercised offline.

    Mirrors `decider_for`'s contract exactly: `none` is a real, offered
    option, for the same reason -- a closed option set with no way to decline
    pushes the model into inventing a family for a request that needed none,
    the same failure `app/design/assertions`'s `UNMEASURED` exists to name in
    a different module.
    """

    def decide(request: str, labels: "tuple[str, ...]") -> str | None:
        if not request.strip() or not labels:
            return None
        active = agent if agent is not None else _get_agent()
        if active is None:
            return None
        criteria = {label: _hint(label) for label in labels}
        criteria[_DECLINE] = "None of the above; this request needs no CATIA or analysis tool"
        started = time.monotonic()
        try:
            result = active.predict(
                state=request,
                questions={
                    "family": {
                        "type": "choice",
                        "instructions": (
                            "Which family of CAD or analysis operations will this "
                            "request need?"
                        ),
                        "criteria": criteria,
                    }
                },
            )
            choice = result["answers"]["family"]["choice"]
        except Exception:
            logger.exception("Laya intent decision raised; keeping the lexical selection")
            return None
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        # Logged unconditionally -- not just on failure -- because a "decided"
        # rule in tool_retrieval's own log is invisible whenever Laya's answer
        # overlaps CORE_TOOLS (routine CATIA modelling always does), which
        # would otherwise make it indistinguishable from Laya never having run.
        if choice not in labels:
            logger.info(
                "Laya decided %r in %sms (declined or unrecognised, of %d labels)",
                choice,
                elapsed_ms,
                len(labels),
            )
            return None
        logger.info("Laya decided %r in %sms (of %d labels)", choice, elapsed_ms, len(labels))
        return str(choice)

    return decide


__all__ = ["MODEL_ID", "SUBFOLDER", "laya_decider"]
