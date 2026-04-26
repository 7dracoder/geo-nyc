"""Unit tests for the OllamaProvider.

We use httpx's ``MockTransport`` (no respx required for these basic
cases) so the tests stay fast and dependency-light.
"""

from __future__ import annotations

import json

import httpx
import pytest

from geo_nyc.ai.providers.ollama import OllamaProvider
from geo_nyc.exceptions import (
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
)


def _make_provider(handler: httpx.MockTransport, *, model: str = "llama3.1:8b") -> OllamaProvider:
    client = httpx.AsyncClient(
        base_url="http://localhost:11434",
        transport=handler,
        timeout=5.0,
    )
    return OllamaProvider(
        base_url="http://localhost:11434",
        model=model,
        timeout_seconds=5.0,
        max_retries=1,
        client=client,
    )


@pytest.mark.asyncio
async def test_generate_returns_text() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "hello world"},
                "done_reason": "stop",
                "total_duration": 1234,
                "eval_count": 7,
                "prompt_eval_count": 3,
            },
        )

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        response = await provider.generate("hi", system_prompt="be brief", temperature=0.1)
    finally:
        await provider.aclose()

    assert response.text == "hello world"
    assert response.model == "llama3.1:8b"
    assert response.metadata["done_reason"] == "stop"
    assert response.metadata["eval_count"] == 7

    body = captured[0]
    assert body["model"] == "llama3.1:8b"
    assert body["stream"] is False
    assert body["options"]["temperature"] == pytest.approx(0.1)
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert body["messages"][1]["role"] == "user"
    assert "format" not in body  # plain mode


@pytest.mark.asyncio
async def test_generate_json_sets_format() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": "{\"ok\": true}"}, "done_reason": "stop"},
        )

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        response = await provider.generate_json("give me json", temperature=0.0)
    finally:
        await provider.aclose()

    assert response.text == '{"ok": true}'
    assert captured[0]["format"] == "json"
    assert captured[0]["options"]["temperature"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_health_check_reports_pulled_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={"models": [{"name": "llama3.1:8b"}, {"name": "phi3"}]},
        )

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        snapshot = await provider.health_check()
    finally:
        await provider.aclose()

    assert snapshot == {
        "status": "ok",
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "llama3.1:8b",
        "model_pulled": True,
        "available_models": ["llama3.1:8b", "phi3"],
    }


@pytest.mark.asyncio
async def test_health_check_handles_unreachable_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        snapshot = await provider.health_check()
    finally:
        await provider.aclose()

    assert snapshot["status"] == "down"
    assert snapshot["provider"] == "ollama"
    assert "Cannot reach Ollama" in snapshot["detail"]


@pytest.mark.asyncio
async def test_5xx_is_retried_then_raises() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, text="overloaded")

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMResponseError):
            await provider.generate("hi")
    finally:
        await provider.aclose()
    assert calls["count"] == 2  # 1 attempt + 1 retry (max_retries=1)


@pytest.mark.asyncio
async def test_4xx_does_not_retry() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, text="bad request")

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMResponseError):
            await provider.generate("hi")
    finally:
        await provider.aclose()
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_timeout_raises_specific_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMTimeoutError):
            await provider.generate("hi")
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_connection_error_raises_specific_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    provider = _make_provider(httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMConnectionError):
            await provider.generate("hi")
    finally:
        await provider.aclose()
