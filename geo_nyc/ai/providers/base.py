"""Abstract LLM provider contract.

Concrete providers live next to this module. Keeping the abstract
interface in its own file lets test code stub providers without pulling
in httpx or any vendor SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class LLMResponse:
    """The full result of a single LLM generation call.

    Concrete providers can attach extra metadata via :attr:`metadata`
    (e.g. token counts, eval times) without breaking the protocol.
    """

    text: str
    model: str
    metadata: dict[str, Any]

    @property
    def stripped(self) -> str:
        """Convenience accessor for trimmed model output."""
        return self.text.strip()


class BaseLLMProvider(ABC):
    """Minimal async contract every provider must implement."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return a JSON-serialisable health snapshot.

        Implementations should never raise on a degraded provider; they
        should report ``{"status": "down", "detail": "..."}`` instead so
        the API layer can surface a 200 with diagnostics for the demo.
        """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Generate text for ``prompt`` and return the full response."""

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        extra_options: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Generate output constrained to a JSON object.

        Implementations should request the provider's JSON mode (Ollama
        ``format=json``) so we don't have to scrape code fences.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Release any underlying network resources."""
