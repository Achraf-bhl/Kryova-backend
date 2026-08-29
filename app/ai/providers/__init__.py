"""Provider construction. The only module that knows which vendors exist."""

from functools import lru_cache

from app.ai.provider import LLMProvider, LLMUnavailable
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "AnthropicProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "PROVIDER_NAMES",
    "get_provider",
]

PROVIDER_NAMES = ("ollama", "anthropic", "openai_compatible")


@lru_cache
def get_provider() -> LLMProvider:
    """Build the configured provider. Cached: construction reads settings only."""
    from app.core.config import settings

    choice = settings.ai_provider.strip().lower()

    if choice == "ollama":
        return OllamaProvider(
            base_url=settings.ai_base_url or "http://localhost:11434",
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    if choice == "anthropic":
        return AnthropicProvider(
            api_key=settings.ai_api_key or "",
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    if choice == "openai_compatible":
        if not settings.ai_base_url:
            raise LLMUnavailable(
                "AI_PROVIDER=openai_compatible requires AI_BASE_URL "
                "(e.g. http://localhost:1234/v1 for LM Studio)."
            )
        return OpenAICompatibleProvider(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )

    raise LLMUnavailable(
        f"Unknown AI_PROVIDER '{settings.ai_provider}'. Valid values: {', '.join(PROVIDER_NAMES)}."
    )
