"""Schema-level tests for the LLM extraction Pydantic models."""

from __future__ import annotations

import json

from geo_nyc.config import REPO_ROOT
from geo_nyc.extraction.structured import LLMExtraction


def test_demo_fixture_validates() -> None:
    fixture = REPO_ROOT / "data/fixtures/nyc_demo/llm_extraction.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    parsed = LLMExtraction.model_validate(payload)

    assert len(parsed.formations) == 3
    names = {f.name for f in parsed.formations}
    assert {"Manhattan Schist", "Inwood Marble", "Glacial Till"} <= names
    assert any(c.depth_unit == "m" for c in parsed.contacts)
    assert any(s.type == "dip" for s in parsed.structures)
    assert all(f.evidence for f in parsed.formations)


def test_blank_quote_rejected() -> None:
    bad = {
        "formations": [
            {
                "name": "Manhattan Schist",
                "evidence": [
                    {
                        "document_id": "d",
                        "page": 1,
                        "quote": "   ",
                    }
                ],
            }
        ],
        "contacts": [],
        "structures": [],
    }
    try:
        LLMExtraction.model_validate(bad)
    except ValueError:
        return
    raise AssertionError("blank quote should have failed validation")


def test_confidence_range_enforced() -> None:
    bad = {
        "formations": [{"name": "Manhattan Schist"}],
        "contacts": [
            {
                "top_formation": "Manhattan Schist",
                "bottom_formation": "Inwood Marble",
                "confidence": 1.5,
            }
        ],
        "structures": [],
    }
    try:
        LLMExtraction.model_validate(bad)
    except ValueError:
        return
    raise AssertionError("confidence > 1.0 should have failed validation")
