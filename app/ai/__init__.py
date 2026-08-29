"""AI features for Kryova.

Layered behind `LLMProvider` (see `provider.py`) so the model that answers is a
deployment choice, not a code dependency. Default is local Ollama.
"""

from app.ai.provider import (
    Completion,
    LLMError,
    LLMProvider,
    LLMRefusal,
    LLMUnavailable,
    TokenUsage,
)
from app.ai.providers import PROVIDER_NAMES, get_provider
from app.ai.schemas import LoadCaseDraft, ResultInterpretation
from app.ai.service import draft_load_case, generate_title, interpret_result

__all__ = [
    "PROVIDER_NAMES",
    "Completion",
    "LLMError",
    "LLMProvider",
    "LLMRefusal",
    "LLMUnavailable",
    "LoadCaseDraft",
    "ResultInterpretation",
    "TokenUsage",
    "draft_load_case",
    "generate_title",
    "get_provider",
    "interpret_result",
]
