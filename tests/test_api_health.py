"""Smoke tests for the health endpoints.

We stub the LLM provider so these tests don't require a running Ollama
server.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from geo_nyc.ai import reset_provider_cache
from geo_nyc.ai.providers.base import BaseLLMProvider, LLMResponse


class _StubProvider(BaseLLMProvider):
    """In-memory provider used by API tests."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot

    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def model_name(self) -> str:
        return self._snapshot.get("model", "stub-model")

    async def health_check(self) -> dict[str, Any]:
        return self._snapshot

    async def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return LLMResponse(text="ok", model=self.model_name, metadata={})

    async def generate_json(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return LLMResponse(text="{}", model=self.model_name, metadata={})

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client_with_stub_provider(
    isolated_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    snapshot = {
        "status": "ok",
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "llama3.1:8b",
        "model_pulled": True,
        "available_models": ["llama3.1:8b"],
    }
    stub = _StubProvider(snapshot)

    reset_provider_cache()
    monkeypatch.setattr("api.routers.health.get_default_provider", lambda: stub)
    monkeypatch.setattr("api.main.get_default_provider", lambda: stub)

    from api.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


def test_health_endpoint(client_with_stub_provider: TestClient) -> None:
    response = client_with_stub_provider.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["use_fixtures"] is True
    assert body["enable_gempy"] is False
    assert "version" in body


def test_llm_health_endpoint(client_with_stub_provider: TestClient) -> None:
    response = client_with_stub_provider.get("/api/llm/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider"] == "ollama"
    assert body["model_pulled"] is True


def test_unknown_run_id_returns_404(client_with_stub_provider: TestClient) -> None:
    response = client_with_stub_provider.get("/api/run/does-not-exist")
    assert response.status_code == 404
