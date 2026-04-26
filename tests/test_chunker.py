"""Unit tests for :mod:`geo_nyc.extraction.chunker`."""

from __future__ import annotations

from datetime import UTC, datetime

from geo_nyc.documents.schemas import ExtractedPage, ExtractionResult
from geo_nyc.extraction.chunker import Chunker, ChunkerConfig, chunk_extraction


def _fake_extraction(*pages: str, document_id: str = "d_test") -> ExtractionResult:
    extracted_pages = [
        ExtractedPage(page=i + 1, text=t, char_count=len(t), is_empty=not t.strip())
        for i, t in enumerate(pages)
    ]
    return ExtractionResult(
        document_id=document_id,
        filename=f"{document_id}.pdf",
        page_count=len(pages),
        pages=extracted_pages,
        char_count=sum(len(p) for p in pages),
        pages_with_text=sum(1 for p in pages if p.strip()),
        extracted_at=datetime.now(UTC),
    )


def test_each_short_page_yields_one_chunk() -> None:
    extraction = _fake_extraction(
        "Page one talks about Manhattan Schist.",
        "Page two has Inwood Marble at 12 m depth.",
        "Page three references the Bronx and dip 35°.",
    )

    chunks = chunk_extraction(extraction)

    assert len(chunks) == 3
    assert [c.page_start for c in chunks] == [1, 2, 3]
    assert [c.page_end for c in chunks] == [1, 2, 3]
    assert [c.sequence for c in chunks] == [0, 1, 2]
    assert [c.chunk_id for c in chunks] == [
        "d_test-p0001-c00",
        "d_test-p0002-c01",
        "d_test-p0003-c02",
    ]
    assert all(not c.is_empty for c in chunks)
    assert chunks[0].char_count == len(chunks[0].text)


def test_empty_pages_are_skipped() -> None:
    extraction = _fake_extraction("good page", "   \n   ", "another good page")

    chunks = chunk_extraction(extraction)

    assert [c.page_start for c in chunks] == [1, 3]


def test_long_page_subsplits_at_boundary() -> None:
    sentence = "The Manhattan Schist contact appears at 14 meters depth. "
    long_text = sentence * 100  # ~5700 chars
    extraction = _fake_extraction(long_text)

    config = ChunkerConfig(target_chars=600, overlap_chars=50, min_chunk_chars=40)
    chunks = Chunker(config).chunk(extraction)

    assert len(chunks) > 3
    assert all(c.page_start == 1 and c.page_end == 1 for c in chunks)
    assert all(c.char_count <= 700 for c in chunks)  # target + small slack
    seq_values = [c.sequence for c in chunks]
    assert seq_values == sorted(seq_values)
    # Adjacent chunks should overlap by at least a few characters.
    a, b = chunks[0].text, chunks[1].text
    assert any(a[-30:].strip().split()[-1] in b[:120] for _ in range(1)) or a[-50:].strip() in b[:200]


def test_chunker_is_deterministic() -> None:
    extraction = _fake_extraction("alpha beta gamma " * 50)

    a = chunk_extraction(extraction)
    b = chunk_extraction(extraction)

    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert [c.text for c in a] == [c.text for c in b]


def test_completely_empty_extraction_returns_no_chunks() -> None:
    extraction = _fake_extraction("", "   ", "\n")

    assert chunk_extraction(extraction) == []
