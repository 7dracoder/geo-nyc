"""Integration tests for the document → chunks → run pipeline."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from geo_nyc.ai import reset_provider_cache
from geo_nyc.ai.providers.base import BaseLLMProvider, LLMResponse
from geo_nyc.documents import get_document_service
from geo_nyc.runs import get_run_service
from tests._pdf_helpers import make_pdf


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


def _seed_extracted_document(tmp_path: Path, isolated_settings: Any) -> str:
    """Upload + extract a small NYC-flavoured PDF and return its document_id."""

    pdf_path = make_pdf(
        tmp_path / "demo.pdf",
        pages=[
            "Introduction page about the city. No depth values here.",
            (
                "The Manhattan Schist contact with the Inwood Marble was logged at 42 m "
                "depth in a borehole near the Bronx. Dip 35 degrees south."
            ),
            "Conclusion page mentioning the East River and tunnel alignment.",
        ],
    )

    docs = get_document_service()
    record = docs.upload(content=pdf_path.read_bytes(), filename="demo.pdf")
    docs.extract(record.document_id)
    return record.document_id


def test_run_without_document_id_has_no_chunks(client: TestClient) -> None:
    body = client.post("/api/run", json={}).json()
    assert body["mode"] == "fixture"
    assert body["chunk_summary"] is None
    assert not any(a["kind"] == "ranked_chunks" for a in body["artifacts"])


def test_run_with_document_id_records_ranked_chunks(
    client: TestClient,
    tmp_path: Path,
    isolated_settings: Any,
) -> None:
    document_id = _seed_extracted_document(tmp_path, isolated_settings)

    response = client.post("/api/run", json={"document_id": document_id})

    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "document_chunks+fixture"
    assert body["status"] == "succeeded"

    chunks_artifact = next(a for a in body["artifacts"] if a["kind"] == "ranked_chunks")
    assert chunks_artifact["filename"] == "ranked_chunks.json"
    assert chunks_artifact["metadata"]["document_id"] == document_id
    assert chunks_artifact["metadata"]["chunk_count"] >= 2

    summary = body["chunk_summary"]
    assert summary is not None
    assert summary["document_id"] == document_id
    assert summary["chunk_count"] >= 2
    assert summary["non_zero_chunks"] >= 1
    assert summary["max_score"] == pytest.approx(1.0, abs=1e-3)
    assert summary["top_chunk_id"].startswith(f"{document_id}-p0002")  # geology-dense page

    run_dir = isolated_settings.runs_dir / body["run_id"]
    on_disk = json.loads((run_dir / "ranked_chunks.json").read_text(encoding="utf-8"))
    assert on_disk["document_id"] == document_id
    assert on_disk["chunks"][0]["score"] == pytest.approx(1.0, abs=1e-3)


def test_run_with_unknown_document_id_returns_404(client: TestClient) -> None:
    response = client.post("/api/run", json={"document_id": "d_does_not_exist"})
    assert response.status_code == 404
    assert "extract" in response.json()["detail"].lower()


def test_run_with_document_id_via_service_persists_artifact(
    isolated_settings: Any,
    tmp_path: Path,
) -> None:
    document_id = _seed_extracted_document(tmp_path, isolated_settings)

    service = get_run_service()
    manifest = service.create_run(request_payload={"document_id": document_id})

    assert manifest.status.value == "succeeded"
    assert manifest.mode == "document_chunks+fixture"
    chunk_artifact = manifest.artifact_by_kind("ranked_chunks")
    assert chunk_artifact is not None
    assert chunk_artifact.filename == "ranked_chunks.json"

    on_disk = isolated_settings.runs_dir / manifest.run_id / "ranked_chunks.json"
    assert on_disk.exists()
    payload = json.loads(on_disk.read_text(encoding="utf-8"))
    assert payload["chunk_count"] == manifest.chunk_summary["chunk_count"]
