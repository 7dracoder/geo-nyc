"""LLM provider layer for geo-nyc.

Two providers are supported:

* ``groq`` — cloud Llama (Llama 3.1 / 3.3 / 4) via the Groq API,
  OpenAI-compatible. **Default.** Requires ``GEO_NYC_GROQ_API_KEY``.
* ``ollama`` — local-only fall-back via a running ``ollama serve``.

The module-level :func:`get_default_provider` returns a singleton wired
from :class:`geo_nyc.config.Settings` so call sites stay short.
"""

from __future__ import annotations

from functools import lru_cache

from geo_nyc.ai.providers.base import BaseLLMProvider
from geo_nyc.ai.providers.groq import GroqProvider
from geo_nyc.ai.providers.ollama import OllamaProvider
from geo_nyc.config import get_settings
from geo_nyc.exceptions import ConfigurationError

__all__ = [
    "BaseLLMProvider",
    "GroqProvider",
    "OllamaProvider",
    "get_default_provider",
    "reset_provider_cache",
]


@lru_cache(maxsize=1)
def get_default_provider() -> BaseLLMProvider:
    """Return the configured default LLM provider.

    The cache is reset by :func:`reset_provider_cache` whenever settings
    change (mainly inside tests).
    """

    settings = get_settings()
    if settings.llm_provider == "groq":
        if not settings.groq_api_key:
            raise ConfigurationError(
                "GEO_NYC_LLM_PROVIDER=groq but GEO_NYC_GROQ_API_KEY is empty. "
                "Set the key on the backend host (do NOT put it in Vercel)."
            )
        return GroqProvider(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            model=settings.groq_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise NotImplementedError(f"Unknown LLM provider: {settings.llm_provider}")


def reset_provider_cache() -> None:
    get_default_provider.cache_clear()
