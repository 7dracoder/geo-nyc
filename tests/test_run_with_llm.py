"""Integration tests for the document → chunks → LLM extraction → run pipeline.

The LLM here is a scripted in-memory stub injected via the run-service
dependency override, so these tests stay fast and deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from geo_nyc.ai.providers.base import BaseLLMProvider, LLMResponse
from geo_nyc.documents import get_document_service
from geo_nyc.extraction import LLMExtractor
from geo_nyc.runs import RunService, get_run_service
from tests._pdf_helpers import make_pdf


class _StubProvider(BaseLLMProvider):
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def model_name(self) -> str:
        return "stub-model"

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "provider": self.provider_name}

    async def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return await self.generate_json(prompt, **kwargs)

    async def generate_json(self, prompt: str, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=json.dumps(self._payload), model=self.model_name, metadata={}
        )

    async def aclose(self) -> None:
        return None


def _seed_document(tmp_path: Path, isolated_settings: Any) -> str:
    pdf_path = make_pdf(
        tmp_path / "demo.pdf",
        pages=[
            "Background page about the city. No depth values here.",
            (
                "The Manhattan Schist contact with the Inwood Marble was logged at 42 m "
                "depth in a borehole near the Bronx. Dip 35 degrees south."
            ),
            "Conclusion page mentioning glacial till at 18 m depth.",
        ],
    )
    docs = get_document_service()
    record = docs.upload(content=pdf_path.read_bytes(), filename="demo.pdf")
    docs.extract(record.document_id)
    return record.document_id


def _payload_for(document_id: str) -> dict[str, Any]:
    """Build a payload that references *the* document_id under test."""

    return {
        "formations": [
            {
                "name": "Manhattan Schist",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [
                    {
                        "document_id": document_id,
                        "page": 2,
                        "quote": "The Manhattan Schist contact with the Inwood Marble was logged at 42 m depth.",
                    }
                ],
            },
            {
                "name": "Inwood Marble",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [
                    {
                        "document_id": document_id,
                        "page": 2,
                        "quote": "The Manhattan Schist contact with the Inwood Marble was logged at 42 m depth.",
                    }
                ],
            },
        ],
        "contacts": [
            {
                "top_formation": "Manhattan Schist",
                "bottom_formation": "Inwood Marble",
                "depth_value": 42.0,
                "depth_unit": "m",
                "location_text": "near the Bronx",
                "confidence": 0.7,
                "evidence": [
                    {
                        "document_id": document_id,
                        "page": 2,
                        "quote": "The Manhattan Schist contact with the Inwood Marble was logged at 42 m depth.",
                    }
                ],
            }
        ],
        "structures": [
            {
                "type": "dip",
                "value_degrees": 35.0,
                "azimuth_degrees": None,
                "formation": "Manhattan Schist",
                "location_text": "Bronx outcrop",
                "evidence": [
                    {
                        "document_id": document_id,
                        "page": 2,
                        "quote": "Dip 35 degrees south.",
                    }
                ],
            }
        ],
        "notes": "test fixture",
    }


@pytest.fixture
def stub_provider_factory():
    """Return a callable that builds a fresh _StubProvider per test."""

    def _factory(payload: dict[str, Any]) -> _StubProvider:
        return _StubProvider(payload)

    return _factory


@pytest.fixture
def client_with_stub_llm(
    isolated_settings: Any,
    stub_provider_factory,
) -> Iterator[tuple[TestClient, dict[str, str]]]:
    document_id_holder: dict[str, str] = {}

    def fresh_service() -> RunService:
        # Built fresh per request so the stub provider sees the right
        # ``document_id`` regardless of the test's seeding order.
        provider = stub_provider_factory(_payload_for(document_id_holder["doc_id"]))
        return RunService(
            settings=isolated_settings,
            llm_extractor=LLMExtractor(provider, settings=isolated_settings),
        )

    from api.main import create_app

    app = create_app()
    # FastAPI captures the dependency callable at route-registration
    # time, so monkeypatching the import is too late. ``dependency_overrides``
    # is the documented hook for swapping a Depends() target in tests.
    app.dependency_overrides[get_run_service] = fresh_service

    with TestClient(app) as c:
        yield c, document_id_holder


def test_run_with_use_llm_runs_extractor_and_records_artifacts(
    client_with_stub_llm: tuple[TestClient, dict[str, str]],
    tmp_path: Path,
    isolated_settings: Any,
) -> None:
    client, holder = client_with_stub_llm
    document_id = _seed_document(tmp_path, isolated_settings)
    holder["doc_id"] = document_id

    response = client.post(
        "/api/run", json={"document_id": document_id, "use_llm": True}
    )

    assert response.status_code == 201
    body = response.json()

    # Phase 6 promotes the run mode to "document_llm_dsl" once the LLM
    # extraction is converted into a parseable, schema-valid DSL program
    # — the mesh + GemPy inputs are now driven by the PDF-derived DSL,
    # so the fixture is reduced to extent + horizon scaffolding.
    assert body["mode"] == "document_llm_dsl"
    assert body["status"] == "succeeded"

    kinds = {a["kind"] for a in body["artifacts"]}
    assert "ranked_chunks" in kinds
    assert "llm_extraction" in kinds
    assert "llm_attempts" in kinds
    assert "generated_dsl" in kinds
    assert "generated_validation_report" in kinds
    assert "gempy_inputs" in kinds

    gempy_summary = body["gempy_inputs_summary"]
    assert gempy_summary is not None
    assert gempy_summary["succeeded"] is True
    assert gempy_summary["demo_ready"] is True
    assert gempy_summary["formations_by_source"].get("extracted", 0) >= 1
    assert gempy_summary["surface_points_by_source"].get("extracted", 0) >= 1

    summary = body["llm_summary"]
    assert summary is not None
    assert summary["succeeded"] is True
    assert summary["document_id"] == document_id
    assert summary["formation_count"] >= 2
    assert summary["contact_count"] >= 1

    dsl_summary = body["dsl_summary"]
    assert dsl_summary is not None
    assert dsl_summary["succeeded"] is True
    assert dsl_summary["rock_count"] >= 2
    assert "R_MANHATTAN_SCHIST" in dsl_summary["rock_ids"]
    assert "R_INWOOD_MARBLE" in dsl_summary["rock_ids"]
    # Both depositions should end up in the chain (Inwood Marble below
    # Manhattan Schist ⇒ Inwood deposited first).
    assert dsl_summary["deposition_ids"][0] == "D_INWOOD_MARBLE"
    assert dsl_summary["deposition_ids"][-1] == "D_MANHATTAN_SCHIST"

    run_dir = isolated_settings.runs_dir / body["run_id"]
    on_disk = json.loads((run_dir / "llm_extraction.json").read_text(encoding="utf-8"))
    assert any(f["name"] == "Manhattan Schist" for f in on_disk["formations"])

    geology_dsl = (run_dir / "geology.dsl").read_text(encoding="utf-8")
    assert 'ROCK R_INWOOD_MARBLE [ name: "Inwood Marble"' in geology_dsl
    assert 'ROCK R_MANHATTAN_SCHIST [ name: "Manhattan Schist"' in geology_dsl
    assert "DEPOSITION D_INWOOD_MARBLE" in geology_dsl
    assert "DEPOSITION D_MANHATTAN_SCHIST" in geology_dsl
    assert "after: D_INWOOD_MARBLE" in geology_dsl

    generated_report = json.loads(
        (run_dir / "validation_report_generated.json").read_text(encoding="utf-8")
    )
    assert generated_report["is_valid"] is True

    attempts_dir = run_dir / "llm_attempts"
    assert attempts_dir.is_dir()
    assert (attempts_dir / "attempt_001_initial.json").is_file()


def test_run_without_use_llm_does_not_call_extractor(
    client_with_stub_llm: tuple[TestClient, dict[str, str]],
    tmp_path: Path,
    isolated_settings: Any,
) -> None:
    client, holder = client_with_stub_llm
    document_id = _seed_document(tmp_path, isolated_settings)
    holder["doc_id"] = document_id

    response = client.post("/api/run", json={"document_id": document_id})

    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "document_chunks+fixture"
    assert body["llm_summary"] is None
    kinds = {a["kind"] for a in body["artifacts"]}
    assert "llm_extraction" not in kinds
