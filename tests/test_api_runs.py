"""Black-box HTTP tests for the run pipeline routes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from geo_nyc.ai import reset_provider_cache
from geo_nyc.ai.providers.base import BaseLLMProvider, LLMResponse


class _StubProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def model_name(self) -> str:
        return "stub-model"

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "provider": "ollama", "base_url": "http://localhost:11434"}

    async def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return LLMResponse(text="ok", model=self.model_name, metadata={})

    async def generate_json(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return LLMResponse(text="{}", model=self.model_name, metadata={})

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client(
    isolated_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    stub = _StubProvider()
    reset_provider_cache()
    monkeypatch.setattr("api.routers.health.get_default_provider", lambda: stub)
    monkeypatch.setattr("api.main.get_default_provider", lambda: stub)

    from api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_post_run_returns_201_with_manifest(client: TestClient) -> None:
    response = client.post("/api/run", json={})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["mode"] == "fixture"
    assert any(a["kind"] == "mesh" for a in body["artifacts"])
    assert body["validation"]["is_valid"] is True


def test_get_run_round_trip_via_http(client: TestClient) -> None:
    created = client.post("/api/run", json={}).json()
    run_id = created["run_id"]
    fetched = client.get(f"/api/run/{run_id}").json()
    assert fetched["run_id"] == run_id
    assert fetched["status"] == "succeeded"


def test_get_run_unknown_id_404(client: TestClient) -> None:
    response = client.get("/api/run/r_nope")
    assert response.status_code == 404


def test_list_runs_includes_created_run(client: TestClient) -> None:
    created = client.post("/api/run", json={}).json()
    response = client.get("/api/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert any(item["run_id"] == created["run_id"] for item in body["items"])


def test_static_mesh_is_fetchable(client: TestClient) -> None:
    body = client.post("/api/run", json={}).json()
    mesh = next(a for a in body["artifacts"] if a["kind"] == "mesh")
    relative = "/static/exports/" + mesh["url"].split("/static/exports/", 1)[1]
    response = client.get(relative)
    assert response.status_code == 200
    assert response.content[:4] == b"glTF"


def test_static_field_is_fetchable(client: TestClient) -> None:
    body = client.post("/api/run", json={}).json()
    field_meta = next(a for a in body["artifacts"] if a["kind"] == "field_meta")
    relative = "/static/fields/" + field_meta["url"].split("/static/fields/", 1)[1]
    response = client.get(relative)
    assert response.status_code == 200
    sidecar = response.json()
    assert sidecar["units"] == "meters_below_surface"
    assert sidecar["run_id"] == body["run_id"]
    assert sidecar["source"] in {"gempy", "rbf", "synthetic", "stub"}
    assert sidecar["schema_version"] == 2
    assert "shape" in sidecar
    assert sidecar["projected_crs"] == sidecar["crs"]
