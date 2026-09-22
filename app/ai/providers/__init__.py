"""Provider construction. The only module that knows which vendors exist."""

from functools import lru_cache

from app.ai.provider import LLMProvider, LLMUnavailable
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.nvidia import NvidiaProvider
from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "NvidiaProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "PROVIDER_NAMES",
    "get_provider",
]

PROVIDER_NAMES = ("ollama", "anthropic", "gemini", "nvidia", "openai_compatible")


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
            vision_model=settings.ai_vision_model,
        )
    if choice == "anthropic":
        return AnthropicProvider(
            api_key=settings.ai_api_key or "",
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            vision_model=settings.ai_vision_model,
        )
    if choice == "gemini":
        if not settings.ai_api_key:
            raise LLMUnavailable(
                "AI_PROVIDER=gemini requires AI_API_KEY. Get one from "
                "aistudio.google.com; AI_BASE_URL is optional and defaults to "
                "Gemini's OpenAI-compatible endpoint."
            )
        return GeminiProvider(
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            base_url=settings.ai_base_url,
            vision_model=settings.ai_vision_model,
        )
    if choice == "nvidia":
        if not settings.ai_api_key:
            raise LLMUnavailable(
                "AI_PROVIDER=nvidia requires AI_API_KEY. Get a free key from "
                "build.nvidia.com; it looks like 'nvapi-...'."
            )
        return NvidiaProvider(
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            # Optional: NVIDIA's hosted endpoint is the default, but the same
            # provider reaches a self-hosted NIM container unchanged.
            base_url=settings.ai_base_url,
            thinking=settings.ai_nvidia_thinking,
            reasoning_budget=settings.ai_nvidia_reasoning_budget,
            vision_model=settings.ai_vision_model,
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
            vision_model=settings.ai_vision_model,
        )

    raise LLMUnavailable(
        f"Unknown AI_PROVIDER '{settings.ai_provider}'. Valid values: {', '.join(PROVIDER_NAMES)}."
    )
