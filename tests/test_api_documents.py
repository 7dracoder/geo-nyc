"""HTTP tests for /api/documents."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from geo_nyc.ai import reset_provider_cache
from geo_nyc.ai.providers.base import BaseLLMProvider, LLMResponse
from tests._pdf_helpers import make_pdf_bytes


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


def _pdf_payload(pages: list[str], name: str = "demo.pdf") -> tuple[str, tuple[str, bytes, str]]:
    return ("file", (name, make_pdf_bytes(pages), "application/pdf"))


def test_upload_then_extract_round_trip(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files=[_pdf_payload(["bedrock at 42ft", "schist exposure"], "report.pdf")],
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["status"] == "uploaded"
    document_id = record["document_id"]

    extract = client.post(f"/api/documents/{document_id}/extract")
    assert extract.status_code == 200, extract.text
    body = extract.json()
    assert body["page_count"] == 2
    assert any("schist" in p["text"].lower() for p in body["pages"])

    fetched = client.get(f"/api/documents/{document_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "extracted"

    cached = client.get(f"/api/documents/{document_id}/extraction")
    assert cached.status_code == 200
    assert cached.json()["page_count"] == 2


def test_list_endpoint_includes_uploaded(client: TestClient) -> None:
    upload = client.post(
        "/api/documents/upload",
        files=[_pdf_payload(["only page"], "single.pdf")],
    ).json()
    listing = client.get("/api/documents").json()
    assert listing["total"] >= 1
    assert any(item["document_id"] == upload["document_id"] for item in listing["items"])


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files=[("file", ("notes.txt", b"plain text not a pdf", "text/plain"))],
    )
    assert response.status_code == 415


def test_upload_is_idempotent(client: TestClient) -> None:
    # Re-use the *same* bytes; PyMuPDF embeds creation timestamps so a
    # second `make_pdf_bytes` call would not produce a byte-identical PDF.
    content = make_pdf_bytes(["dup test"])
    a = client.post(
        "/api/documents/upload",
        files=[("file", ("dup.pdf", content, "application/pdf"))],
    ).json()
    b = client.post(
        "/api/documents/upload",
        files=[("file", ("dup-renamed.pdf", content, "application/pdf"))],
    ).json()
    assert a["document_id"] == b["document_id"]


def test_extract_unknown_returns_404(client: TestClient) -> None:
    response = client.post("/api/documents/d_does_not_exist/extract")
    assert response.status_code == 404


def test_extraction_before_extract_returns_404(client: TestClient) -> None:
    upload = client.post(
        "/api/documents/upload",
        files=[_pdf_payload(["needs extract"], "p.pdf")],
    ).json()
    response = client.get(f"/api/documents/{upload['document_id']}/extraction")
    assert response.status_code == 404
