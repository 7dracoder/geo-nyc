"""LLM provider layer for geo-nyc.

Only Ollama is supported. The module-level :func:`get_default_provider`
returns a singleton wired from :class:`geo_nyc.config.Settings` so call
sites stay short.
"""

from __future__ import annotations

from functools import lru_cache

from geo_nyc.ai.providers.base import BaseLLMProvider
from geo_nyc.ai.providers.ollama import OllamaProvider
from geo_nyc.config import get_settings

__all__ = ["BaseLLMProvider", "OllamaProvider", "get_default_provider", "reset_provider_cache"]


@lru_cache(maxsize=1)
def get_default_provider() -> BaseLLMProvider:
    """Return the configured default LLM provider.

    The cache is reset by :func:`reset_provider_cache` whenever settings
    change (mainly inside tests).
    """

    settings = get_settings()
    if settings.llm_provider == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    # The Settings type already constrains llm_provider to a single
    # literal, but we still raise explicitly so future additions get a
    # loud failure here instead of silent dispatch.
    raise NotImplementedError(f"Unknown LLM provider: {settings.llm_provider}")


def reset_provider_cache() -> None:
    get_default_provider.cache_clear()
