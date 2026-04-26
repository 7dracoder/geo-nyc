"""Groq provider.

Groq exposes an OpenAI-compatible chat completions API at
``https://api.groq.com/openai/v1`` and serves Meta's Llama family
(``llama-3.1-*``, ``llama-3.3-*``, ``llama-4-*``) at very high speed.
We talk to it directly with ``httpx`` instead of pulling in the OpenAI
SDK so:

* the request shape stays explicit and easy to mock with ``respx``;
* we don't get pinned to a third-party SDK's release cadence;
* the JSON-mode wire format matches OpenAI's
  ``response_format={"type": "json_object"}`` exactly, so the
  :meth:`generate_json` contract stays identical to
  :class:`~geo_nyc.ai.providers.ollama.OllamaProvider`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from geo_nyc.ai.providers.base import BaseLLMProvider, LLMResponse
from geo_nyc.exceptions import (
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
)
from geo_nyc.logging import get_logger

log = get_logger(__name__)

_DEFAULT_RETRIES = 2
_RETRY_BACKOFF_SECONDS = (0.5, 1.5)


class GroqProvider(BaseLLMProvider):
    """Async client for Groq's OpenAI-compatible chat API.

    The provider owns one :class:`httpx.AsyncClient` whose lifecycle ends
    at :meth:`aclose`; safe to share across requests.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.groq.com/openai/v1",
        model: str = "llama-3.3-70b-versatile",
        timeout_seconds: float = 120.0,
        max_retries: int = _DEFAULT_RETRIES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "GroqProvider requires an API key. Set GEO_NYC_GROQ_API_KEY."
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        self._owns_client = client is None

    # ---- BaseLLMProvider --------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self._model

    async def health_check(self) -> dict[str, Any]:
        """Probe ``GET /models`` to confirm the key works.

        Reports ``model_pulled`` (whether ``self._model`` shows up in the
        models list) so the demo dashboard can warn the operator before a
        run if they typo'd the model id.
        """
        try:
            response = await self._client.get("/models")
            response.raise_for_status()
        except httpx.RequestError as exc:
            return {
                "status": "down",
                "provider": self.provider_name,
                "base_url": self._base_url,
                "detail": f"Cannot reach Groq: {exc}",
            }
        except httpx.HTTPStatusError as exc:
            return {
                "status": "degraded",
                "provider": self.provider_name,
                "base_url": self._base_url,
                "detail": f"Groq returned {exc.response.status_code}",
            }

        payload = response.json()
        items = payload.get("data") or []
        models = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return {
            "status": "ok",
            "provider": self.provider_name,
            "base_url": self._base_url,
            "model": self._model,
            "model_pulled": self._model in models,
            "available_models": models,
        }

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
        return await self._chat(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            json_mode=False,
            extra_options=extra_options,
        )

    async def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        extra_options: dict[str, Any] | None = None,
    ) -> LLMResponse:
        return await self._chat(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=None,
            json_mode=True,
            extra_options=extra_options,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- internals --------------------------------------------------------

    async def _chat(
        self,
        *,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
        json_mode: bool,
        extra_options: dict[str, Any] | None,
    ) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        if stop:
            body["stop"] = list(stop)
        if json_mode:
            # OpenAI-compatible JSON mode. Groq enforces this server-side
            # the same way OpenAI does — model output is guaranteed to
            # parse with `json.loads`.
            body["response_format"] = {"type": "json_object"}
        if extra_options:
            body.update(extra_options)

        payload = await self._post_with_retry("/chat/completions", body)

        choices = payload.get("choices") or []
        if not choices:
            raise LLMResponseError(
                f"Groq /chat/completions returned no choices; got {payload!r}"
            )
        message = choices[0].get("message") or {}
        text = message.get("content", "")
        if not isinstance(text, str):
            raise LLMResponseError(
                f"Unexpected Groq response shape: missing string content; got {payload!r}"
            )

        usage = payload.get("usage") or {}
        metadata: dict[str, Any] = {
            "finish_reason": choices[0].get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "x_groq": payload.get("x_groq"),
        }
        return LLMResponse(text=text, model=self._model, metadata=metadata)

    async def _post_with_retry(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self._max_retries:
            try:
                response = await self._client.post(path, json=body)
            except httpx.TimeoutException as exc:
                last_exc = exc
                log.warning("Groq %s timed out (attempt %d)", path, attempt + 1)
            except httpx.RequestError as exc:
                last_exc = exc
                log.warning("Groq %s connection error (attempt %d): %s", path, attempt + 1, exc)
            else:
                if response.status_code >= 500 or response.status_code == 429:
                    # Retry on server errors and rate-limits; the demo
                    # path never bursts hard, but Groq applies per-second
                    # caps even on the free tier.
                    last_exc = LLMResponseError(
                        f"Groq {path} returned {response.status_code}: {response.text[:300]}"
                    )
                    log.warning(
                        "Groq %s returned %d (attempt %d)",
                        path,
                        response.status_code,
                        attempt + 1,
                    )
                elif response.status_code >= 400:
                    raise LLMResponseError(
                        f"Groq {path} returned {response.status_code}: {response.text[:300]}"
                    )
                else:
                    return response.json()

            attempt += 1
            if attempt > self._max_retries:
                break
            backoff = _RETRY_BACKOFF_SECONDS[
                min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
            ]
            await asyncio.sleep(backoff)

        if isinstance(last_exc, httpx.TimeoutException):
            raise LLMTimeoutError(
                f"Groq {path} timed out after {self._timeout_seconds}s"
            ) from last_exc
        if isinstance(last_exc, httpx.RequestError):
            raise LLMConnectionError(
                f"Cannot reach Groq at {self._base_url}: {last_exc}"
            ) from last_exc
        if isinstance(last_exc, LLMResponseError):
            raise last_exc
        raise LLMResponseError(f"Groq {path} failed without raising")  # pragma: no cover
