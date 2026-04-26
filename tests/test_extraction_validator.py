"""Unit tests for :mod:`geo_nyc.extraction.validator`."""

from __future__ import annotations

from datetime import UTC, datetime

from geo_nyc.documents.schemas import ExtractedPage, ExtractionResult
from geo_nyc.extraction.chunker import chunk_extraction
from geo_nyc.extraction.relevance import score_chunks
from geo_nyc.extraction.structured import (
    Contact,
    EvidenceRef,
    Formation,
    LLMExtraction,
    Structure,
)
from geo_nyc.extraction.validator import validate_extraction


def _ranked():
    pages = [
        "Manhattan Schist outcrop dominates the surface near the Bronx.",
        "The contact with the Inwood Marble lies at 42 m depth in northern Manhattan.",
    ]
    extraction_pdf = ExtractionResult(
        document_id="d_test",
        filename="t.pdf",
        page_count=len(pages),
        pages=[
            ExtractedPage(page=i + 1, text=t, char_count=len(t), is_empty=False)
            for i, t in enumerate(pages)
        ],
        char_count=sum(len(p) for p in pages),
        pages_with_text=len(pages),
        extracted_at=datetime.now(UTC),
    )
    return score_chunks("d_test", chunk_extraction(extraction_pdf))


def _evidence(page: int = 2, chunk_id: str = "d_test-p0002-c01") -> EvidenceRef:
    return EvidenceRef(
        document_id="d_test",
        page=page,
        quote="The contact with the Inwood Marble lies at 42 m depth in northern Manhattan.",
        chunk_id=chunk_id,
    )


def test_well_formed_extraction_validates() -> None:
    ranked = _ranked()
    extraction = LLMExtraction(
        formations=[
            Formation(name="Manhattan Schist", rock_type="metamorphic", evidence=[_evidence(1, ranked.chunks[1].chunk_id if ranked.chunks[1].page_start == 1 else ranked.chunks[0].chunk_id)]),
            Formation(name="Inwood Marble", rock_type="metamorphic", evidence=[_evidence()]),
        ],
        contacts=[
            Contact(
                top_formation="Manhattan Schist",
                bottom_formation="Inwood Marble",
                depth_value=42.0,
                depth_unit="m",
                evidence=[_evidence()],
            )
        ],
        structures=[
            Structure(type="dip", value_degrees=35.0, formation="Manhattan Schist", evidence=[_evidence()])
        ],
    )

    report = validate_extraction(extraction, document_id="d_test", ranked_chunks=ranked)

    assert report.is_valid is True
    assert report.meets_demo_minimum is True
    assert report.normalized is not None
    assert report.normalized.contacts[0].depth_unit == "m"


def test_feet_depth_normalised_to_metres() -> None:
    ranked = _ranked()
    chunk_id = ranked.chunks[0].chunk_id
    extraction = LLMExtraction(
        formations=[
            Formation(name="Manhattan Schist", evidence=[_evidence(chunk_id=chunk_id)]),
            Formation(name="Inwood Marble", evidence=[_evidence(chunk_id=chunk_id)]),
        ],
        contacts=[
            Contact(
                top_formation="Manhattan Schist",
                bottom_formation="Inwood Marble",
                depth_value=100.0,
                depth_unit="ft",
                evidence=[_evidence(chunk_id=chunk_id)],
            )
        ],
    )

    report = validate_extraction(extraction, document_id="d_test", ranked_chunks=ranked)

    assert report.is_valid is True
    contact = report.normalized.contacts[0]
    assert contact.depth_unit == "m"
    assert abs(contact.depth_value - 30.48) < 1e-3


def test_dip_out_of_range_is_error() -> None:
    ranked = _ranked()
    chunk_id = ranked.chunks[0].chunk_id
    extraction = LLMExtraction(
        formations=[
            Formation(name="Manhattan Schist", evidence=[_evidence(chunk_id=chunk_id)]),
            Formation(name="Inwood Marble", evidence=[_evidence(chunk_id=chunk_id)]),
        ],
        contacts=[],
        structures=[
            Structure(
                type="dip",
                value_degrees=120.0,
                formation="Manhattan Schist",
                evidence=[_evidence(chunk_id=chunk_id)],
            )
        ],
    )

    report = validate_extraction(extraction, document_id="d_test", ranked_chunks=ranked)

    assert report.is_valid is False
    assert any("dip must be in" in e for e in report.errors)
    assert report.normalized is None


def test_evidence_chunk_id_unknown_is_error() -> None:
    ranked = _ranked()
    bad_evidence = EvidenceRef(
        document_id="d_test",
        page=1,
        quote="Made up quote",
        chunk_id="d_test-p9999-c99",
    )
    extraction = LLMExtraction(
        formations=[
            Formation(name="Manhattan Schist", evidence=[bad_evidence]),
            Formation(name="Inwood Marble", evidence=[bad_evidence]),
        ],
        contacts=[
            Contact(
                top_formation="Manhattan Schist",
                bottom_formation="Inwood Marble",
                evidence=[bad_evidence],
            )
        ],
    )

    report = validate_extraction(extraction, document_id="d_test", ranked_chunks=ranked)

    assert report.is_valid is False
    assert any("not present in the source chunks" in e for e in report.errors)


def test_contact_top_equals_bottom_is_error() -> None:
    ranked = _ranked()
    chunk_id = ranked.chunks[0].chunk_id
    extraction = LLMExtraction(
        formations=[
            Formation(name="Manhattan Schist", evidence=[_evidence(chunk_id=chunk_id)]),
        ],
        contacts=[
            Contact(
                top_formation="Manhattan Schist",
                bottom_formation="manhattan schist",
                evidence=[_evidence(chunk_id=chunk_id)],
            )
        ],
    )
    report = validate_extraction(extraction, document_id="d_test", ranked_chunks=ranked)
    assert report.is_valid is False
    assert any("must differ" in e for e in report.errors)


def test_demo_minimum_warning_when_only_one_formation() -> None:
    ranked = _ranked()
    chunk_id = ranked.chunks[0].chunk_id
    extraction = LLMExtraction(
        formations=[Formation(name="Manhattan Schist", evidence=[_evidence(chunk_id=chunk_id)])],
    )
    report = validate_extraction(extraction, document_id="d_test", ranked_chunks=ranked)
    assert report.is_valid is True  # missing demo minimum is a warning, not an error
    assert report.meets_demo_minimum is False
    assert any("Demo minimum" in w for w in report.warnings)
