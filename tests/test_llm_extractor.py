"""Tests for :class:`LLMExtractor`, exercised end-to-end with a stub provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from geo_nyc.ai.providers.base import BaseLLMProvider, LLMResponse
from geo_nyc.documents.schemas import ExtractedPage, ExtractionResult
from geo_nyc.extraction.chunker import chunk_extraction
from geo_nyc.extraction.llm_extractor import (
    ExtractionRunResult,
    LLMExtractor,
)
from geo_nyc.extraction.relevance import score_chunks
from geo_nyc.extraction.schemas import RankedChunks


class _ScriptedProvider(BaseLLMProvider):
    """Returns a pre-baked sequence of LLM responses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []  # (system, user)

    @property
    def provider_name(self) -> str:
        return "scripted"

    @property
    def model_name(self) -> str:
        return "scripted-model"

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "provider": self.provider_name}

    async def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return await self.generate_json(prompt, **kwargs)

    async def generate_json(self, prompt: str, **kwargs: Any) -> LLMResponse:
        self.calls.append((kwargs.get("system_prompt", ""), prompt))
        if not self._responses:
            raise AssertionError("No more scripted responses available")
        text = self._responses.pop(0)
        return LLMResponse(text=text, model=self.model_name, metadata={"scripted": True})

    async def aclose(self) -> None:
        return None


def _build_ranked() -> RankedChunks:
    pages = [
        "Background page about the city. No depth values here.",
        (
            "The Manhattan Schist contact with the Inwood Marble was logged at 42 m depth "
            "in a borehole near the Bronx. Dip 35° to the south."
        ),
        "Conclusion page mentioning glacial till at 18 m depth.",
    ]
    pdf = ExtractionResult(
        document_id="d_doc",
        filename="d.pdf",
        page_count=len(pages),
        pages=[
            ExtractedPage(page=i + 1, text=t, char_count=len(t), is_empty=False)
            for i, t in enumerate(pages)
        ],
        char_count=sum(len(p) for p in pages),
        pages_with_text=len(pages),
        extracted_at=datetime.now(UTC),
    )
    return score_chunks("d_doc", chunk_extraction(pdf))


def _good_payload(ranked: RankedChunks) -> dict[str, Any]:
    primary_chunk = ranked.chunks[0].chunk_id
    second_chunk = ranked.chunks[1].chunk_id if len(ranked.chunks) > 1 else primary_chunk
    return {
        "formations": [
            {
                "name": "Manhattan Schist",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [
                    {
                        "document_id": "d_doc",
                        "page": ranked.chunks[0].page_start,
                        "quote": "The Manhattan Schist contact with the Inwood Marble was logged at 42 m depth.",
                        "chunk_id": primary_chunk,
                    }
                ],
            },
            {
                "name": "Inwood Marble",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [
                    {
                        "document_id": "d_doc",
                        "page": ranked.chunks[0].page_start,
                        "quote": "The contact between Manhattan Schist and Inwood Marble was logged at 42 m depth.",
                        "chunk_id": primary_chunk,
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
                        "document_id": "d_doc",
                        "page": ranked.chunks[0].page_start,
                        "quote": "The Manhattan Schist contact with the Inwood Marble was logged at 42 m depth.",
                        "chunk_id": primary_chunk,
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
                "location_text": "near the Bronx",
                "evidence": [
                    {
                        "document_id": "d_doc",
                        "page": ranked.chunks[0].page_start,
                        "quote": "Dip 35° to the south.",
                        "chunk_id": second_chunk,
                    }
                ],
            }
        ],
        "notes": "fixture",
    }


def _bad_payload_top_equals_bottom(ranked: RankedChunks) -> dict[str, Any]:
    payload = _good_payload(ranked)
    payload["contacts"][0]["bottom_formation"] = payload["contacts"][0]["top_formation"]
    return payload


@pytest.mark.asyncio
async def test_extractor_succeeds_on_first_attempt(
    isolated_settings: Any, tmp_path: Path
) -> None:
    ranked = _build_ranked()
    provider = _ScriptedProvider([json.dumps(_good_payload(ranked))])
    extractor = LLMExtractor(provider, settings=isolated_settings)

    result: ExtractionRunResult = await extractor.extract(
        ranked, document_id="d_doc", run_dir=tmp_path
    )

    assert result.succeeded is True
    assert result.extraction is not None
    assert len(result.attempts) == 1
    assert result.attempts[0].role == "initial"
    assert result.validation.is_valid is True
    assert (tmp_path / "llm_attempts" / "attempt_001_initial.json").exists()


@pytest.mark.asyncio
async def test_extractor_repairs_invalid_first_attempt(
    isolated_settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_NYC_LLM_MAX_REPAIR_ATTEMPTS", "2")
    from geo_nyc.config import get_settings, reset_settings_cache

    reset_settings_cache()
    settings = get_settings()
    settings.ensure_directories()

    ranked = _build_ranked()
    bad = json.dumps(_bad_payload_top_equals_bottom(ranked))
    good = json.dumps(_good_payload(ranked))
    provider = _ScriptedProvider([bad, good])

    extractor = LLMExtractor(provider, settings=settings)
    result = await extractor.extract(ranked, document_id="d_doc", run_dir=tmp_path)

    assert result.succeeded is True
    assert len(result.attempts) == 2
    assert result.attempts[0].role == "initial"
    assert result.attempts[1].role == "repair"
    assert result.validation.is_valid is True
    assert (tmp_path / "llm_attempts" / "attempt_002_repair.json").exists()


@pytest.mark.asyncio
async def test_extractor_records_failure_when_repairs_exhausted(
    isolated_settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_NYC_LLM_MAX_REPAIR_ATTEMPTS", "1")
    from geo_nyc.config import get_settings, reset_settings_cache

    reset_settings_cache()
    settings = get_settings()
    settings.ensure_directories()

    ranked = _build_ranked()
    bad = json.dumps(_bad_payload_top_equals_bottom(ranked))
    provider = _ScriptedProvider([bad, bad])

    extractor = LLMExtractor(provider, settings=settings)
    result = await extractor.extract(ranked, document_id="d_doc", run_dir=tmp_path)

    assert result.succeeded is False
    assert len(result.attempts) == 2
    assert result.validation is not None
    assert any("must differ" in e for e in result.validation.errors)
    assert (tmp_path / "llm_attempts" / "attempt_001_initial.json").exists()
    assert (tmp_path / "llm_attempts" / "attempt_002_repair.json").exists()


@pytest.mark.asyncio
async def test_extractor_recovers_when_model_emits_prose_around_json(
    isolated_settings: Any, tmp_path: Path
) -> None:
    ranked = _build_ranked()
    payload = json.dumps(_good_payload(ranked))
    wrapped = f"Here is your JSON:\n```json\n{payload}\n```\nThanks!"
    provider = _ScriptedProvider([wrapped])

    extractor = LLMExtractor(provider, settings=isolated_settings)
    result = await extractor.extract(ranked, document_id="d_doc", run_dir=tmp_path)

    assert result.succeeded is True


@pytest.mark.asyncio
async def test_extractor_records_unparseable_output(
    isolated_settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_NYC_LLM_MAX_REPAIR_ATTEMPTS", "0")
    from geo_nyc.config import get_settings, reset_settings_cache

    reset_settings_cache()
    settings = get_settings()
    settings.ensure_directories()

    ranked = _build_ranked()
    provider = _ScriptedProvider(["totally not json"])
    extractor = LLMExtractor(provider, settings=settings)
    result = await extractor.extract(ranked, document_id="d_doc", run_dir=tmp_path)

    assert result.succeeded is False
    assert result.attempts[0].parse_error is not None
    assert result.attempts[0].parsed is None
