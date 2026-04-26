"""Ollama provider.

Talks to a local ``ollama serve`` (default ``http://localhost:11434``)
via its native HTTP API. We deliberately call the raw endpoints rather
than depend on the ``ollama`` Python SDK so:

* the wire format stays explicit and easy to mock with ``respx``;
* we don't get pinned to whatever version the SDK ships next month;
* the timeout / retry logic stays under our control.
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


class OllamaProvider(BaseLLMProvider):
    """Async client for the Ollama HTTP API.

    The provider is safe to share across requests; it owns a single
    :class:`httpx.AsyncClient` whose lifecycle ends at :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_retries: int = _DEFAULT_RETRIES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        # Tests can inject a pre-configured AsyncClient (or a respx mock
        # transport) without subclassing.
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._owns_client = client is None

    # ---- BaseLLMProvider ---------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    async def health_check(self) -> dict[str, Any]:
        """Probe ``/api/tags`` to verify the server is reachable.

        Reports ``model_pulled`` so the demo dashboard can warn the
        operator before they kick off a run.
        """
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
        except httpx.RequestError as exc:
            return {
                "status": "down",
                "provider": self.provider_name,
                "base_url": self._base_url,
                "detail": f"Cannot reach Ollama: {exc}",
            }
        except httpx.HTTPStatusError as exc:
            return {
                "status": "degraded",
                "provider": self.provider_name,
                "base_url": self._base_url,
                "detail": f"Ollama returned {exc.response.status_code}",
            }

        payload = response.json()
        models = [m.get("name") for m in payload.get("models", []) if m.get("name")]
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

        options: dict[str, Any] = {
            "temperature": float(temperature),
            "num_predict": int(max_tokens),
        }
        if stop:
            options["stop"] = list(stop)
        if extra_options:
            options.update(extra_options)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if json_mode:
            body["format"] = "json"

        payload = await self._post_with_retry("/api/chat", body)

        message = payload.get("message", {}) or {}
        text = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(text, str):
            raise LLMResponseError(
                f"Unexpected Ollama response shape: missing string content; got {payload!r}"
            )

        metadata: dict[str, Any] = {
            "done_reason": payload.get("done_reason"),
            "total_duration_ns": payload.get("total_duration"),
            "eval_count": payload.get("eval_count"),
            "prompt_eval_count": payload.get("prompt_eval_count"),
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
                log.warning("Ollama %s timed out (attempt %d)", path, attempt + 1)
            except httpx.RequestError as exc:
                last_exc = exc
                log.warning("Ollama %s connection error (attempt %d): %s", path, attempt + 1, exc)
            else:
                if response.status_code >= 500:
                    last_exc = LLMResponseError(
                        f"Ollama {path} returned {response.status_code}: {response.text[:300]}"
                    )
                    log.warning(
                        "Ollama %s returned %d (attempt %d)",
                        path,
                        response.status_code,
                        attempt + 1,
                    )
                elif response.status_code >= 400:
                    raise LLMResponseError(
                        f"Ollama {path} returned {response.status_code}: {response.text[:300]}"
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
                f"Ollama {path} timed out after {self._timeout_seconds}s"
            ) from last_exc
        if isinstance(last_exc, httpx.RequestError):
            raise LLMConnectionError(f"Cannot reach Ollama at {self._base_url}: {last_exc}") from last_exc
        if isinstance(last_exc, LLMResponseError):
            raise last_exc
        # Defensive default; we should never reach this branch.
        raise LLMResponseError(f"Ollama {path} failed without raising")  # pragma: no cover
