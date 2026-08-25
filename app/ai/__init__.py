"""AI features for Kryova.

Layered behind `LLMProvider` (see `provider.py`) so the model that answers is a
deployment choice, not a code dependency. Default is local Ollama.
"""

from app.ai.provider import LLMError, LLMProvider, LLMRefusal, LLMUnavailable
from app.ai.providers import PROVIDER_NAMES, get_provider
from app.ai.schemas import LoadCaseDraft, ResultInterpretation
from app.ai.service import draft_load_case, interpret_result

__all__ = [
    "PROVIDER_NAMES",
    "LLMError",
    "LLMProvider",
    "LLMRefusal",
    "LLMUnavailable",
    "LoadCaseDraft",
    "ResultInterpretation",
    "draft_load_case",
    "get_provider",
    "interpret_result",
]
