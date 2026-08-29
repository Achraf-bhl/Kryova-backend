"""AI features, expressed against the provider seam.

Nothing here knows which model is answering. Every entry point follows the same
shape: build the volatile half of the prompt, hand the frozen system prompt to
the provider, return what came back together with what it cost. The usage half
is not optional -- see `usage.py` for why LLM spend is metered the way FEA
compute is.
"""

import json
from typing import Any

from app.ai import prompts
from app.ai.provider import Completion, LLMError, LLMProvider, TokenUsage
from app.ai.sanitise import sanitise_untrusted
from app.ai.schemas import LoadCaseDraft, ResultInterpretation
from app.core.config import settings

#: Titles are one short line; anything more is the model ignoring the prompt.
TITLE_MAX_TOKENS = 60
#: Hard ceiling on the stored title, matching the column.
TITLE_MAX_CHARS = 60


def _result_payload(
    result: dict[str, Any],
    load_case: dict[str, Any],
    mesh_stats: dict[str, Any] | None,
    element_size_mm: float | None,
) -> str:
    """The numbers the model is allowed to talk about, and nothing else.

    Assembled explicitly rather than dumping the ORM row: an unfiltered dump
    would leak ids and timestamps into the prompt, and every extra key is one
    more thing the model can mistake for a physical quantity.
    """
    material = load_case.get("material") or {}
    payload = {
        "factor_of_safety": result.get("factor_of_safety"),
        "yields": result.get("yields"),
        "max_von_mises_mpa": result.get("max_von_mises_mpa"),
        "max_displacement_mm": result.get("max_displacement_mm"),
        "mass_kg": result.get("mass_kg"),
        "volume_mm3": result.get("volume_mm3"),
        "material": {
            "name": material.get("name"),
            "yield_strength_mpa": material.get("yield_strength_mpa"),
            "youngs_modulus_mpa": material.get("youngs_modulus_mpa"),
        },
        "mesh": {
            "element_count": result.get("element_count"),
            "node_count": result.get("node_count"),
            "element_size_mm": element_size_mm,
            "stats": mesh_stats or {},
        },
        "load_case": {
            "name": load_case.get("name"),
            "fixtures": load_case.get("fixtures"),
            "loads": load_case.get("loads"),
        },
        "solver_warnings": result.get("warnings") or [],
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def interpret_result(
    provider: LLMProvider,
    *,
    result: dict[str, Any],
    load_case: dict[str, Any],
    mesh_stats: dict[str, Any] | None = None,
    element_size_mm: float | None = None,
) -> Completion[ResultInterpretation]:
    """Explain a finished linear static run in engineering terms."""
    return provider.complete(
        system=prompts.INTERPRET_SYSTEM,
        user=prompts.interpret_user_message(
            _result_payload(result, load_case, mesh_stats, element_size_mm)
        ),
        schema=ResultInterpretation,
        effort=settings.ai_effort_interpret,
        max_tokens=settings.ai_max_tokens,
    )


def draft_load_case(
    provider: LLMProvider,
    *,
    description: str,
    bounding_box: dict[str, Any],
) -> Completion[LoadCaseDraft]:
    """Turn a sentence into a load case the solver can run.

    The result is a *draft*: it comes back with its assumptions and unresolved
    questions attached, and the caller is expected to show both rather than
    submitting it straight to the solver.
    """
    return provider.complete(
        system=prompts.PARSE_LOAD_CASE_SYSTEM,
        user=prompts.parse_load_case_user_message(
            description=description,
            bounding_box=json.dumps(bounding_box, indent=2, sort_keys=True, default=str),
        ),
        schema=LoadCaseDraft,
        effort=settings.ai_effort_parse,
        max_tokens=settings.ai_max_tokens,
    )


def _fallback_title(user_message: str) -> str:
    """The truncation the model call is trying to beat.

    Kept as the fallback because a truncated prompt, however ugly, is still the
    user's own words -- which beats "New conversation" for finding a session in
    a sidebar three days later.
    """
    cleaned = " ".join(sanitise_untrusted(user_message, max_chars=400).split())
    return cleaned[:TITLE_MAX_CHARS].strip() or "New conversation"


def generate_title(
    provider: LLMProvider, *, user_message: str, assistant_reply: str
) -> tuple[str, TokenUsage]:
    """Name a conversation from its first exchange.

    Deliberately not a structured-output call: one short line does not need a
    schema, and `chat` is the one method every provider is guaranteed to
    implement usefully with an empty tool list.

    Never raises. A sidebar label is not worth failing a turn over, so any
    provider failure -- unavailable, refused, malformed -- falls back to the
    truncated prompt the product used before.
    """
    try:
        turn = provider.chat(
            system=prompts.TITLE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": prompts.title_user_message(
                        sanitise_untrusted(user_message, max_chars=2_000),
                        sanitise_untrusted(assistant_reply, max_chars=2_000),
                    ),
                }
            ],
            tools=[],
            max_tokens=TITLE_MAX_TOKENS,
        )
    except LLMError:
        return _fallback_title(user_message), TokenUsage()

    # Models like to wrap a title in quotes however firmly the prompt says not
    # to, and a leading quote in a sidebar reads as a bug.
    title = " ".join(turn.text.split()).strip().strip('"').strip("'")
    if not title:
        return _fallback_title(user_message), turn.usage
    return title[:TITLE_MAX_CHARS], turn.usage
